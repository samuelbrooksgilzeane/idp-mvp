import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useParams } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { ReviewNavigation } from "./ReviewNavigation";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function Detail() {
  const { id = "" } = useParams(); const location = useLocation();
  return <><p>{id}</p><ReviewNavigation id={id} kind="results" /><output>{JSON.stringify(location.state)}</output></>;
}
it("crosses a cursor boundary while retaining filters and explicit run identities", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true,
    json: async () => ({ items: [{ extraction_run_id: "historical" }], next_cursor: null }) })));
  render(<MemoryRouter initialEntries={[{ pathname: "/results/first", state: { list: {
    kind: "results", url: "/results?schema=invoice&latest=false", endpoint: "/api/extractions",
    query: "limit=50&schema_id=invoice&latest_only=false", ids: ["first"], next: "cursor-two",
  } } }]}><Routes><Route path="/results/:id" element={<Detail />} /></Routes></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: "Next result" }));
  await waitFor(() => expect(screen.getByText("historical")).toBeInTheDocument());
  expect(fetch).toHaveBeenCalledWith("/api/extractions?limit=50&schema_id=invoice&latest_only=false&cursor=cursor-two");
  expect(screen.getByRole("link", { name: "Back to results" }).getAttribute("href")).toContain("latest=false");
});
it("does not guess neighbors for a deep link", () => {
  render(<MemoryRouter><ReviewNavigation id="historical" kind="results" /></MemoryRouter>);
  expect(screen.queryByRole("button", { name: "Next result" })).toBeNull();
});
