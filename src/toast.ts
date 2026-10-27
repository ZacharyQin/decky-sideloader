import { toaster } from "@decky/api";
import { recordFrontendError } from "./diagnosticsController";

export function toastError(error: unknown): void {
  recordFrontendError(error, "toast");
  toaster.toast({
    title: "Sideloader",
    body: error instanceof Error ? error.message : String(error),
  });
}

export function toastInfo(body: string): void {
  toaster.toast({
    title: "Sideloader",
    body,
  });
}
