import { ChevronLeft, ChevronRight } from "lucide-react";

type PaginationProps = {
  page: number;
  pageCount: number;
  itemCount: number;
  itemLabel: string;
  onPageChange: (page: number) => void;
  hasMore?: boolean;
  onLoadNext?: () => void;
  loading?: boolean;
};

export function Pagination({
  page,
  pageCount,
  itemCount,
  itemLabel,
  onPageChange,
  hasMore = false,
  onLoadNext,
  loading = false,
}: PaginationProps) {
  if (!itemCount) return null;

  const first = (page - 1) * 10 + 1;
  const last = Math.min(page * 10, itemCount);
  const pages = Array.from({ length: pageCount }, (_, index) => index + 1);

  return (
    <nav className="pagination" aria-label={`${itemLabel} pagination`}>
      <span className="pagination-summary">
        {first}–{last} of {itemCount}{hasMore ? "+" : ""} {itemLabel}
      </span>
      <div className="pagination-pages">
        <button
          type="button"
          aria-label="Previous page"
          disabled={page === 1 || loading}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft size={14} aria-hidden="true" />
        </button>
        {pages.map((number) => (
          <button
            type="button"
            key={number}
            className={number === page ? "active" : undefined}
            aria-label={`Page ${number}`}
            aria-current={number === page ? "page" : undefined}
            onClick={() => onPageChange(number)}
          >
            {number}
          </button>
        ))}
        <button
          type="button"
          aria-label="Next page"
          disabled={(page === pageCount && !hasMore) || loading}
          onClick={() => {
            if (page < pageCount) onPageChange(page + 1);
            else onLoadNext?.();
          }}
        >
          <ChevronRight size={14} aria-hidden="true" />
        </button>
      </div>
    </nav>
  );
}
