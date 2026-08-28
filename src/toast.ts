import { toaster } from "@decky/api";

export function toastError(error: unknown): void {
  toaster.toast({
    title: "GitHub Sideload",
    body: error instanceof Error ? error.message : String(error),
  });
}

export function toastInfo(body: string): void {
  toaster.toast({
    title: "GitHub Sideload",
    body,
  });
}
