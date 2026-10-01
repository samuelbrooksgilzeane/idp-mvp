import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FolderImportController } from "../hooks/useFolderImport";
import { UploadPanel } from "./UploadPanel";

function controller(changes: Partial<FolderImportController> = {}): FolderImportController {
  return { batch: null, folders: null, root: null, busy: false, running: false, error: null,
    loadFolders: vi.fn(async () => {}), start: vi.fn(async () => {}), retry: vi.fn(async () => {}),
    clear: vi.fn(), refresh: vi.fn(async () => {}), ...changes };
}

afterEach(cleanup);

describe("upload panel folder import", () => {
  it("is absent unless the deployment enables folder import", () => {
    render(<UploadPanel uploading={false} notice={null} onUpload={vi.fn()} />);
    expect(screen.queryByText("Import from folder")).not.toBeInTheDocument();
  });

  it("loads folders when opened and imports the chosen folder with the case ID", () => {
    const closed = controller();
    const { rerender, container } = render(<UploadPanel uploading={false} notice={null} onUpload={vi.fn()} folderImport={closed} />);
    const details = container.querySelector("details.folder-import") as HTMLDetailsElement;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    expect(closed.loadFolders).toHaveBeenCalledOnce();

    const loaded = controller({ folders: ["invoices", "receipts"], root: "/Volumes/c/s/idp_import" });
    rerender(<UploadPanel uploading={false} notice={null} onUpload={vi.fn()} folderImport={loaded} />);
    expect(screen.getByText(/Reach out to a member of\s+the support team if you'd like to upload a folder/)).toBeInTheDocument();
    expect(screen.queryByText(/databricks fs cp/)).not.toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Import folder" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Case ID/), { target: { value: "CASE-9" } });
    fireEvent.change(screen.getByLabelText("Folder"), { target: { value: "receipts" } });
    fireEvent.click(button);
    expect(loaded.start).toHaveBeenCalledWith("receipts", "CASE-9");
  });
});
