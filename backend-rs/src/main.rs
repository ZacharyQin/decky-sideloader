use serde_json::{Value, json};
use sideloader_core::{common::*, diagnostics::REQUEST, engine::Engine};
use std::{io::Read, sync::Arc};
use tokio::{
    io::{AsyncBufRead, AsyncBufReadExt, AsyncWriteExt, BufReader},
    sync::{Semaphore, mpsc},
};
const MAX_FRAME: usize = 32 * 1024 * 1024;
async fn frame<R: AsyncBufRead + Unpin>(reader: &mut R) -> Result<Option<Value>> {
    let mut data = Vec::new();
    loop {
        let buffer = reader.fill_buf().await?;
        if buffer.is_empty() {
            ensure(data.is_empty(), "IPC 消息不完整")?;
            return Ok(None);
        }
        let n = buffer
            .iter()
            .position(|c| *c == b'\n')
            .map_or(buffer.len(), |n| n + 1);
        ensure(
            data.len().saturating_add(n) <= MAX_FRAME,
            "IPC 消息超过上限",
        )?;
        let complete = buffer[n - 1] == b'\n';
        data.extend_from_slice(&buffer[..n]);
        reader.consume(n);
        if complete {
            let v: Value = serde_json::from_slice(&data)?;
            ensure(v.is_object(), "IPC 消息必须是对象")?;
            return Ok(Some(v));
        }
    }
}
fn encoded_frame(value: &Value) -> std::io::Result<Vec<u8>> {
    let mut data = serde_json::to_vec(value).map_err(|e| std::io::Error::other(e.to_string()))?;
    if data.len() >= MAX_FRAME {
        return Err(std::io::Error::other("IPC response limit"));
    }
    data.push(b'\n');
    Ok(data)
}
fn binary_hash() -> Result<String> {
    let mut file = std::fs::File::open("/proc/self/exe")?;
    use sha2::{Digest, Sha256};
    let mut hash = Sha256::new();
    let mut buffer = vec![0; 1024 * 1024];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    Ok(hex::encode(hash.finalize()))
}
fn identity(hash: &str) -> Value {
    json!({"protocol":1,"version":env!("CARGO_PKG_VERSION"),"fingerprint":env!("SIDELOADER_BUILD"),"target":env!("SIDELOADER_TARGET"),"sha256":hash})
}
#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() {
    std::panic::set_hook(Box::new(|_| {
        eprintln!("{{\"level\":\"error\",\"code\":\"panic\"}}")
    }));
    if let Err(error) = run().await {
        eprintln!("{}", json!({"level":"error","code":error.code}));
        std::process::exit(1);
    }
}
async fn run() -> Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let hash = binary_hash()?;
    if args == ["--identity"] {
        println!("{}", identity(&hash));
        return Ok(());
    }
    ensure(
        args.len() == 3 && args[0] == "--stdio" && args[1] == "--parent-pid",
        "Rust 后端启动参数无效",
    )?;
    let parent = args[2]
        .parse::<libc::pid_t>()
        .map_err(|_| Error::invalid("父进程标识无效"))?;
    // The kernel death signal plus EOF cover forced Loader termination and clean disconnects.
    ensure(
        unsafe { libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM) } == 0,
        "无法绑定插件进程生命周期",
    )?;
    ensure(unsafe { libc::getppid() } == parent, "插件父进程已变化")?;
    let mut reader = BufReader::new(tokio::io::stdin());
    let init = frame(&mut reader)
        .await?
        .ok_or_else(|| Error::invalid("缺少 IPC 初始化"))?;
    ensure(
        init["type"] == "init" && init["protocol"] == 1,
        "IPC 协议版本不兼容",
    )?;
    let host: Host = serde_json::from_value(init["host"].clone())?;
    let (tx, mut rx) = mpsc::channel::<Value>(16);
    let core = Engine::new(host, tx.clone(), hash.clone())?;
    let writer_stop = core.stop.clone();
    let writer = tokio::spawn(async move {
        let mut output = tokio::io::stdout();
        while let Some(value) = rx.recv().await {
            let closing = value["type"] == "closed";
            let data = encoded_frame(&value)?;
            if let Err(e) = output.write_all(&data).await {
                writer_stop.cancel();
                return Err(e);
            }
            output.flush().await?;
            if closing {
                break;
            }
        }
        Ok::<_, std::io::Error>(())
    });
    let mut hello = identity(&hash);
    hello["type"] = json!("hello");
    hello["boot_id"] = json!(core.boot);
    tx.send(hello)
        .await
        .map_err(|_| Error::invalid("IPC 连接已关闭"))?;
    core.diagnostics.record("startup", json!({}));
    core.begin();
    let permits = Arc::new(Semaphore::new(16));
    let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    let mut shutdown_id = None;
    loop {
        let message = tokio::select! {_=core.stop.cancelled()=>break,_=term.recv()=>break,_=tokio::signal::ctrl_c()=>break,v=frame(&mut reader)=>match v{Ok(Some(v))=>v,Ok(None)=>break,Err(_)=>{core.stop.cancel();break;}}};
        if message["type"] == "host_log" {
            core.diagnostics
                .record("sdk_log", message["fields"].clone());
            continue;
        }
        let request_id = text(&message["id"]).to_owned();
        let method = text(&message["method"]).to_owned();
        if message["type"] != "request" || !valid_id(&request_id) || !message["args"].is_array() {
            let _=tx.send(json!({"type":"response","id":request_id,"ok":false,"error":{"code":"invalid_input","message":"IPC 请求无效"}})).await;
            continue;
        }
        if method == "shutdown" {
            shutdown_id = Some(request_id);
            break;
        }
        let callback = method == "receive_native_management";
        let permit = if callback {
            None
        } else {
            match permits.clone().try_acquire_owned() {
                Ok(p) => Some(p),
                Err(_) => {
                    let _=tx.send(json!({"type":"response","id":request_id,"ok":false,"error":{"code":"busy","message":"后端请求过多，请稍后重试"}})).await;
                    continue;
                }
            }
        };
        let engine = core.clone();
        let sender = tx.clone();
        let args = array(&message["args"]).to_vec();
        let context = json!({"requestId":request_id});
        core.tasks.spawn(REQUEST.scope(context,async move{let _permit=permit;let quiet=["get_operation","get_self_update_status"].contains(&method.as_str());let started=std::time::Instant::now();if !quiet{engine.diagnostics.record("rpc_start",json!({"rpc":method}));}let result=engine.dispatch(&method,&args).await;match &result{Ok(_)=>if !quiet{engine.diagnostics.record("rpc_end",json!({"rpc":method,"durationMs":started.elapsed().as_millis()as u64}));},Err(e)=>engine.diagnostics.record("rpc_failed",json!({"rpc":method,"durationMs":started.elapsed().as_millis()as u64,"errorCode":e.code}))};let response=match result{Ok(value)=>json!({"type":"response","id":request_id,"ok":true,"result":value}),Err(error)=>json!({"type":"response","id":request_id,"ok":false,"error":error})};let bounded=if serde_json::to_vec(&response).is_ok_and(|v|v.len()<MAX_FRAME){response}else{json!({"type":"response","id":request_id,"ok":false,"error":{"code":"response_limit","message":"后端响应超过上限"}})};let _=sender.send(bounded).await;}));
    }
    core.shutdown().await;
    if let Some(id) = shutdown_id {
        let _ = tx
            .send(json!({"type":"response","id":id,"ok":true,"result":null}))
            .await;
    }
    let _ = tx.send(json!({"type":"closed"})).await;
    drop(tx);
    writer
        .await
        .map_err(|_| Error::invalid("IPC 输出任务失败"))??;
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn invalid_and_truncated_frames() {
        let mut r = BufReader::new(&b"[]\n"[..]);
        assert!(frame(&mut r).await.is_err());
        let mut r = BufReader::new(&b"{\"id\":1}"[..]);
        assert!(frame(&mut r).await.is_err());
        let mut r = BufReader::new(&b"{\"id\":1}\n"[..]);
        assert_eq!(frame(&mut r).await.unwrap().unwrap()["id"], 1);
    }
    #[tokio::test]
    async fn fragmented_unicode_and_multiple_frames_preserve_boundaries() {
        let bytes = "{\"文\":7}\n{\"second\":true}\n".as_bytes();
        let mut reader = BufReader::with_capacity(1, bytes);
        assert_eq!(frame(&mut reader).await.unwrap().unwrap()["文"], 7);
        assert_eq!(frame(&mut reader).await.unwrap().unwrap()["second"], true);
        assert!(frame(&mut reader).await.unwrap().is_none());
    }
    #[tokio::test]
    async fn frame_limit_accepts_exact_boundary_and_rejects_one_more_byte() {
        let mut exact = b"{\"p\":\"".to_vec();
        exact.resize(MAX_FRAME - 3, b'x');
        exact.extend_from_slice(b"\"}\n");
        assert_eq!(exact.len(), MAX_FRAME);
        let mut reader = BufReader::new(exact.as_slice());
        assert_eq!(
            frame(&mut reader).await.unwrap().unwrap()["p"]
                .as_str()
                .unwrap()
                .len(),
            MAX_FRAME - 9
        );
        exact.insert(6, b'x');
        let mut reader = BufReader::new(exact.as_slice());
        assert_eq!(
            frame(&mut reader).await.unwrap_err().message,
            "IPC 消息超过上限"
        );
    }
    #[tokio::test]
    async fn response_frame_budget_includes_the_delimiter() {
        let exact = json!({"p":"x".repeat(MAX_FRAME - 9)});
        let encoded = encoded_frame(&exact).unwrap();
        assert_eq!(encoded.len(), MAX_FRAME);
        assert!(frame(&mut BufReader::new(encoded.as_slice())).await.is_ok());
        let oversized = json!({"p":"x".repeat(MAX_FRAME - 8)});
        assert!(
            encoded_frame(&oversized).is_err(),
            "writer accepted a frame the reader rejects"
        );
    }
    #[tokio::test]
    async fn malformed_utf8_scalars_and_excessive_depth_fail_bounded() {
        for bytes in [
            b"\n".as_slice(),
            b"true\n",
            b"null\n",
            b"{\"p\":\"\xff\"}\n",
        ] {
            assert!(frame(&mut BufReader::new(bytes)).await.is_err());
        }
        let nested = format!("{{\"p\":{}0{}}}\n", "[".repeat(200), "]".repeat(200));
        assert!(frame(&mut BufReader::new(nested.as_bytes())).await.is_err());
    }
}
