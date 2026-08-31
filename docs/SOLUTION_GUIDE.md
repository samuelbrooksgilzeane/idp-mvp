# IDP MVP Solution Guide

Last reviewed: 2026-08-31

This is the presentation-oriented guide to the current application. It explains the system at a
level that is useful in a technical walkthrough without requiring the audience to know the source
tree. For implementation history and deployment evidence, use
[`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md). For the performance and simplification backlog, use
[`PERFORMANCE_AND_SIMPLIFICATION_REVIEW.md`](PERFORMANCE_AND_SIMPLIFICATION_REVIEW.md).

## The solution in one minute

The application turns source PDFs into reviewable, governed extraction results.

1. A user uploads one or more PDFs and optionally assigns a case.
2. A parsing job retains the document text, layout elements and page images.
3. An extraction job loads an approved versioned schema, calls `ai_extract`, and retains both the
   raw model response and flattened field/evidence rows.
4. Deterministic validation evaluates provenance, schema integrity, evidence, confidence and the
   rules declared by that exact schema version.
5. The user reviews values beside the source page, follows citations to the relevant region, and
   exports selected runs.

The central design choice is immutability: parsing, extraction and validation create new attempts
instead of overwriting history. A result can therefore always be tied back to the source document,
parse attempt, extraction schema and validation run that produced it.

## System architecture

```mermaid
flowchart LR
    User["User"] --> React["React + TypeScript UI"]
    React -->|"/api"| API["FastAPI application"]

    subgraph App["Application service layer"]
        Docs["Documents"]
        Parse["Parsing"]
        Extract["Extraction"]
        Validate["Validation"]
        Results["Results and export"]
        Schemas["Schema governance"]
    end

    API --> Docs
    API --> Parse
    API --> Extract
    API --> Validate
    API --> Results
    API --> Schemas

    subgraph Databricks["Databricks"]
        Jobs["Jobs: parse and extract"]
        SQL["SQL warehouse"]
        Delta["Unity Catalog Delta tables and views"]
        Volumes["Source and artifact volumes"]
        AI["ai_parse_document + ai_extract"]
    end

    Parse --> Jobs
    Extract --> Jobs
    Jobs --> AI
    Jobs --> Delta
    Docs --> Volumes
    Docs --> SQL
    Schemas --> SQL
    Validate --> SQL
    Results --> SQL
    SQL --> Delta
    API -->|"authenticated page image stream"| Volumes
```

The same service interfaces also have SQLite and local-filesystem implementations. That mock path
is valuable because it exercises the API and UI without Databricks credentials; it is an adapter,
not a separate application.

## End-to-end workflow

```mermaid
sequenceDiagram
    actor User
    participant UI as React UI
    participant API as FastAPI
    participant Job as Databricks Job
    participant UC as Unity Catalog
    participant AI as Document AI

    User->>UI: Upload PDF
    UI->>API: POST /api/documents
    API->>UC: Store PDF and registry row

    User->>UI: Parse selected documents
    UI->>API: POST /api/batches/parse
    API->>Job: Submit trusted document inputs
    Job->>AI: ai_parse_document 2.0
    AI-->>Job: Text, layout and page artifacts
    Job->>UC: Retain immutable parse result

    User->>UI: Extract with schema version
    UI->>API: POST /api/batches/extract
    API->>Job: Submit document and schema identity
    Job->>UC: Reload and hash-check schema
    Job->>AI: ai_extract 2.1
    AI-->>Job: Values, confidence and citations
    Job->>UC: Retain raw result and flattened fields

    User->>UI: Review result
    UI->>API: GET result and evidence
    API->>UC: Read run, fields, schema and page metadata
    API-->>UI: Values linked to source boxes

    User->>UI: Validate or export
    UI->>API: Run deterministic validation or build export
    API->>UC: Read retained, versioned evidence
    API-->>UI: Review outcome or workbook
```

## Governed data model

The diagram shows the logical ownership rather than every column.

```mermaid
erDiagram
    DOCUMENT ||--o{ PARSE_RUN : has
    DOCUMENT ||--o{ EXTRACTION_RUN : has
    PARSE_RUN ||--o{ EXTRACTION_RUN : supplies
    SCHEMA_VERSION ||--o{ EXTRACTION_RUN : governs
    EXTRACTION_RUN ||--o{ EXTRACTED_RECORD : contains
    EXTRACTED_RECORD ||--o{ EXTRACTED_RECORD : nests
    EXTRACTED_RECORD ||--o{ EXTRACTED_FIELD : contains
    EXTRACTION_RUN ||--o{ VALIDATION_RUN : evaluated_by
    VALIDATION_RUN ||--o{ VALIDATION_RESULT : produces
    EXTRACTION_RUN ||--o{ INVOICE_CANDIDATE : optionally_projects
    INVOICE_CANDIDATE ||--o{ INVOICE_LINE_CANDIDATE : has

    DOCUMENT {
        string document_id PK
        string case_id
        string source_path "server-side only"
        string status
    }
    PARSE_RUN {
        string parse_run_id PK
        string document_id FK
        variant parsed
        string status
    }
    SCHEMA_VERSION {
        string schema_id PK
        int schema_version PK
        string schema_hash
        string status
    }
    EXTRACTION_RUN {
        string extraction_run_id PK
        string document_id FK
        string parse_run_id FK
        string schema_id FK
        int schema_version FK
        variant ai_result
        string status
    }
    EXTRACTED_RECORD {
        string record_id PK
        string parent_record_id FK
        string instance_path
    }
    EXTRACTED_FIELD {
        string extraction_run_id FK
        string instance_path
        variant value
        double confidence_score
        variant citations
    }
    VALIDATION_RUN {
        string validation_run_id PK
        string extraction_run_id FK
        string document_status
    }
    VALIDATION_RESULT {
        string validation_run_id FK
        string rule_id
        string status
        string severity
    }
```

There are three representations of extraction data, each serving a different need:

| Representation | Purpose | Retention rule |
|---|---|---|
| Raw `ai_result` | Auditability and faithful reconstruction | Always retained on the immutable extraction run |
| Generic records and fields | Schema-independent review and export | One record tree for any nested schema shape |
| Invoice candidate tables | Convenient typed analytics for the current invoice use case | A projection, never the authoritative model result |

## How the results experience works today

The list and detail pages have different responsibilities.

- `/results` lists extraction attempts and lets the user filter and select runs for export.
- `/results/:runId` reconstructs the hierarchical result, displays values and evidence, and keeps
  the source page beside the result.
- `/documents/:documentId` is the workflow view: parse, extract and validate one document while
  retaining its source viewer.

On a direct visit to the results list, the browser currently starts health, document, case and
extraction-list requests. The extraction-list service then performs several Databricks reads of
its own. This is the main performance boundary discussed in the companion review.

```mermaid
sequenceDiagram
    participant Browser
    participant App as FastAPI
    participant SQL as Databricks SQL

    par App shell startup
        Browser->>App: GET /api/health
        Browser->>App: GET /api/documents
        App->>SQL: Read document registry
        Browser->>App: GET /api/documents/cases
        App->>SQL: Read distinct cases
    and Results page
        Browser->>App: GET /api/extractions
        App->>SQL: Read run metadata
        App->>SQL: Read documents again
        App->>SQL: Count cached root records
        App->>SQL: Read all field issue signals
        loop Each distinct schema version
            App->>SQL: Read schema
        end
    end
```

## Source-code map

| Area | Entry point | Main responsibility |
|---|---|---|
| Application shell | `frontend/src/App.tsx` | Routing, runtime status and document/case state |
| Documents list | `frontend/src/pages/DocumentsPage.tsx` | Upload, case/status/search filters and batch selection |
| Document workflow | `frontend/src/pages/DocumentDetailPage.tsx` | Source viewer with extraction, validation and history tabs |
| Results list | `frontend/src/pages/ResultsPage.tsx` | Run filtering, selection and generic export |
| Result review | `frontend/src/pages/ResultDetailPage.tsx` | Hierarchical result beside citation evidence |
| HTTP API | `backend/src/idp_app/api/` | Public routes, request validation and response models |
| Service composition | `backend/src/idp_app/api/dependencies.py` | Chooses mock or Databricks adapters and constructs services |
| Workflow services | `backend/src/idp_app/services/` | Use cases, repositories, validation, projections and exports |
| Databricks tasks | `databricks_etl/src/` | Code executed by parsing, extraction and schema-registration jobs |
| Governed objects | `databricks_etl/sql/` | Delta tables, migrations and derived views |
| Deployment | `databricks_etl/resources/` | Asset Bundle jobs and Databricks App resource |

When following a request through the code, use this order:

```text
React page/component -> api/<feature>.py -> <feature> service -> repository protocol
                     -> SQLite adapter (mock) or Databricks adapter (deployed)
```

## Design decisions worth explaining

### Immutable attempts

Retries do not mutate prior results. This makes failures inspectable and makes “latest” a query,
not destructive state.

### Governed schema identity

The browser sends a schema ID and version, not arbitrary extraction JSON. The job reloads the
registered contract and checks its hash before extraction. That closes the gap between what the
user saw and what the model executed.

### Evidence stays attached to values

Confidence is metadata, not correctness. Citations link a value back to a page and bounding box;
the source PDF and internal volume paths stay behind the API.

### Generic core, optional typed projection

The generic record tree supports arbitrary nested schemas. Invoice candidate tables are an
analytics convenience and can be retired or replaced without losing the retained model result.

### Synchronous reads, asynchronous processing

Parsing and extraction use Jobs because they are long-running and retryable. Registry, result and
validation reads are synchronous API operations. This boundary is simple, but it means list APIs
must avoid multiple sequential warehouse statements.

## A seven-minute presentation outline

1. **Problem (45 seconds):** document extraction must be reviewable and auditable, not just a model
   response.
2. **Journey (90 seconds):** upload, parse, extract, inspect evidence, validate, export.
3. **Architecture (90 seconds):** React and FastAPI orchestrate; Databricks Jobs do long-running
   work; Unity Catalog retains governed state and artifacts.
4. **Trust model (90 seconds):** immutable attempts, hashed schemas, server-owned paths, citations
   and deterministic validation.
5. **Data model (60 seconds):** raw response for audit, generic tree for any schema, typed invoice
   projection for reporting.
6. **Current engineering focus (45 seconds):** make results reads one bounded query and remove the
   duplicate legacy/generic paths.
7. **Close (30 seconds):** the MVP proves the governed workflow; the next work makes it faster and
   easier to own without changing that contract.

## Operational points not to miss

- Batch `for_each` concurrency is explicitly configured; its platform default would be serial.
- `app.yaml` must remain independently bootable. A non-bundle deployment reads it directly.
- Replacing a Unity Catalog view can remove the App's grant; the documented two-pass deployment
  reapplies bindings.
- Source PDFs, internal volume paths, raw parser output and credentials are never public API data.
- The local mock is a development adapter. A deployed App must report `mode=databricks` on
  `/api/health`.
