import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

import { invalidateDocumentReviews } from "../lib/extractionReviewPrefetch";

// The review and history caches are module-level, so each test starts without another's responses.
afterEach(() => invalidateDocumentReviews());
