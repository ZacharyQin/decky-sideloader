from functools import wraps
import time
import uuid
import inspect
import re
from sideload.diagnostic_context import CONTEXT, error_fields, identifier


def _observed_rpc(method):
    failure_only = method.__name__ in {"get_operation", "get_self_update_status"}
    signature = inspect.signature(method)
    @wraps(method)
    async def observed(self, *args, **kwargs):
        started = time.monotonic()
        parent = CONTEXT.get()
        context = {key: value for key, value in parent.items() if key in ("operationId", "pluginId", "repoId", "releaseId")}
        context.update(requestId=uuid.uuid4().hex, rpc=method.__name__)
        try:
            bound = signature.bind(self, *args, **kwargs).arguments
            for parameter, kind, key in (("name", "plugin", "pluginId"), ("existing_name", "plugin", "pluginId"), ("repo", "repo", "repoId"), ("tag", "release", "releaseId")):
                if bound.get(parameter): context[key] = identifier(kind, bound[parameter])
            operation = bound.get("identifier")
            if type(operation) is str and re.fullmatch(r"[a-f0-9]{32}", operation) and method.__name__ != "ownership_native_step": context["operationId"] = operation
        except TypeError:
            pass  # Let the original RPC report its own argument error.
        if parent.get("requestId"): context["parentId"] = parent["requestId"]
        token = CONTEXT.set(context)
        try:
            if not failure_only:
                self._diagnostic("rpc_start", **context)
            try:
                result = await method(self, *args, **kwargs)
            except BaseException as error:
                self._diagnostic("rpc_failed", **context, durationMs=max(0, int((time.monotonic() - started) * 1000)), **error_fields(error))
                raise
            if method.__name__ == "prepare_install" and type(result) is dict:
                context["operationId"] = result.get("id")
            if not failure_only:
                self._diagnostic("rpc_end", **context, durationMs=max(0, int((time.monotonic() - started) * 1000)))
            return result
        finally:
            CONTEXT.reset(token)
    return observed
