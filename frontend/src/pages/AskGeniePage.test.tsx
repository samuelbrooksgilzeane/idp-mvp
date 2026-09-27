import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AskGeniePage } from "./AskGeniePage";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function configure(enabled: boolean, embed_url: string | null = null) {
  const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    project_name: "Project", genie: { enabled, embed_url,
      open_url: "https://example.cloud.databricks.com/genie/rooms/abc",
      coverage: "Project results. App selections do not filter Genie." },
  }) });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
it("leaves the iframe absent when disabled", async () => {
  const fetcher = configure(false);
  const { container } = render(<AskGeniePage />);
  expect(await screen.findByText("Genie is not configured for this project yet.")).toBeTruthy();
  expect(container.querySelector("iframe")).toBeNull();
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it("provides a fallback without embedding configured", async () => {
  configure(true);
  render(<AskGeniePage />);
  expect(await screen.findByRole("link", { name: "Open Genie in Databricks" })).toHaveAttribute("rel", "noopener noreferrer");
  expect(screen.queryByTitle("Project Genie")).toBeNull();
});
it("loads the configured frame and preserves its fallback on failure", async () => {
  configure(true, "https://example.cloud.databricks.com/embed/genie/rooms/abc");
  const { unmount } = render(<AskGeniePage />);
  const frame = await screen.findByTitle("Project Genie");
  expect(frame).toHaveAttribute("allow", "clipboard-write");
  fireEvent.click(screen.getByRole("button", { name: "Hide embedded view" }));
  expect(screen.getByText("The embedded view is hidden or unavailable.")).toBeTruthy();
  expect(screen.getByRole("link")).toBeTruthy();
  unmount();
  expect(screen.queryByTitle("Project Genie")).toBeNull();
});
it("shows configuration errors", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
  render(<AskGeniePage />);
  expect(await screen.findByRole("alert")).toHaveTextContent("could not be loaded");
});
