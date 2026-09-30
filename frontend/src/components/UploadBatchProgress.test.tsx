import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { UploadBatchController, UploadItem } from "../hooks/useUploadBatch";
import { UploadBatchProgress } from "./UploadBatchProgress";

afterEach(cleanup);

describe("large upload progress", () => {
  it("renders only 25 of 1,000 outcomes and makes every page reachable", () => {
    const items: UploadItem[] = Array.from({ length: 1000 }, (_, ordinal) => ({
      client_file_id: String(ordinal), name: `synthetic-${ordinal}.pdf`, size: 9,
      last_modified: 123, ordinal, state: "REGISTERED", document_id: `doc-${ordinal}`,
      attempts: 1, error_code: null, error_message: null, retryable: false, updated_at: "",
    }));
    const upload: UploadBatchController = {
      batch: { client_request_id: "synthetic", case_id: null, batch_id: "batch", files: items, items },
      busy: false, paused: false, error: null, maxFiles: 1000, maxFileBytes: null, parallelTransfers: 3,
      start: vi.fn(), retry: vi.fn(), pause: vi.fn(), clear: vi.fn(), refresh: vi.fn(),
    };
    render(<MemoryRouter><UploadBatchProgress upload={upload} /></MemoryRouter>);
    expect(screen.getByRole("status")).toHaveTextContent("1000 of 1000 uploaded");
    expect(screen.getByRole("button", { name: "Previous files" })).toBeDisabled();
    for (let page = 0; page < 40; page++) {
      expect(screen.getAllByRole("listitem")).toHaveLength(25);
      expect(screen.getByText(`synthetic-${page * 25}.pdf`)).toBeInTheDocument();
      expect(screen.getByText(`Page ${page + 1} of 40`)).toBeInTheDocument();
      if (page < 39) fireEvent.click(screen.getByRole("button", { name: "Next files" }));
    }
    expect(screen.getByText("synthetic-999.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next files" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Previous files" }));
    expect(screen.getByText("Page 39 of 40")).toBeInTheDocument();
  });

  it("describes a server-side folder import without browser transfer controls", () => {
    const items: UploadItem[] = [
      { client_file_id: "a", name: "a.pdf", relative_path: "2026/a.pdf", size: 9, last_modified: null, ordinal: 0,
        state: "REGISTERED", document_id: "doc-a", attempts: 1, error_code: null, error_message: null, retryable: false, updated_at: "" },
      { client_file_id: "b", name: "b.pdf", relative_path: "2026/b.pdf", size: 9, last_modified: null, ordinal: 1,
        state: "FAILED", document_id: null, attempts: 1, error_code: "IMPORT_FILE_MISSING",
        error_message: "The file is no longer in the import folder.", retryable: true, updated_at: "" },
    ];
    const retry = vi.fn();
    render(<MemoryRouter><UploadBatchProgress source="folder" upload={{
      batch: { case_id: null, folder: "invoices", skipped_files: 3, items }, busy: false, running: false,
      error: null, retry, clear: vi.fn(),
    }} /></MemoryRouter>);
    expect(screen.getByRole("status")).toHaveTextContent("1 of 2 imported");
    expect(screen.getByText("Folder: invoices · 3 non-PDF files skipped")).toBeInTheDocument();
    expect(screen.getByText(/you can close this tab/)).toBeInTheDocument();
    expect(screen.getByText("2026/b.pdf")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause uploads" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry unfinished files" }));
    expect(retry).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "New folder import" })).toBeEnabled();
  });
});
