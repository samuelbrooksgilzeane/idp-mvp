import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";

afterEach(cleanup);

describe("Markdown", () => {
  it("renders a table, list, bold and code as elements", () => {
    const { container } = render(<Markdown text={[
      "Totals for **ACME**:",
      "",
      "| Supplier | Currency | Total |",
      "|---|---|---:|",
      "| ACME | GBP | `1,640.00` |",
      "",
      "- first",
      "- second",
    ].join("\n")} />);

    expect(screen.getByRole("columnheader", { name: "Supplier" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "1,640.00" }).querySelector("code")).toBeTruthy();
    expect(container.querySelector("strong")?.textContent).toBe("ACME");
    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual(["first", "second"]);
  });

  it("never turns answer text into HTML or links", () => {
    const { container } = render(<Markdown text={'<img src=x onerror="alert(1)"> [doc](https://evil.example)'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("<img src=x");
  });
});
