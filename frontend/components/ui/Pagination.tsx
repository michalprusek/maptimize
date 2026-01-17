"use client";

import { motion } from "framer-motion";
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";
import { layoutTransition, buttonHoverProps } from "@/lib/animations";

interface PaginationProps {
  currentPage: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  totalItems: number;
  itemsPerPage: number;
  /** Optional: show items per page selector */
  showPageSizeSelector?: boolean;
  pageSizeOptions?: number[];
  onPageSizeChange?: (size: number) => void;
}

export function Pagination({
  currentPage,
  totalPages,
  onPageChange,
  totalItems,
  itemsPerPage,
  showPageSizeSelector = false,
  pageSizeOptions = [24, 48, 96, 192],
  onPageSizeChange,
}: PaginationProps): JSX.Element | null {
  if (totalPages <= 1) return null;

  const startItem = (currentPage - 1) * itemsPerPage + 1;
  const endItem = Math.min(currentPage * itemsPerPage, totalItems);

  // Generate page numbers to show
  const getPageNumbers = (): (number | "...")[] => {
    const pages: (number | "...")[] = [];
    const maxVisible = 5;

    if (totalPages <= maxVisible + 2) {
      // Show all pages if there aren't many
      for (let i = 1; i <= totalPages; i++) {
        pages.push(i);
      }
    } else {
      // Always show first page
      pages.push(1);

      if (currentPage > 3) {
        pages.push("...");
      }

      // Show pages around current
      const start = Math.max(2, currentPage - 1);
      const end = Math.min(totalPages - 1, currentPage + 1);

      for (let i = start; i <= end; i++) {
        pages.push(i);
      }

      if (currentPage < totalPages - 2) {
        pages.push("...");
      }

      // Always show last page
      pages.push(totalPages);
    }

    return pages;
  };

  return (
    <div className="flex items-center justify-between gap-4 py-4">
      {/* Items info */}
      <div className="text-sm text-text-muted">
        {startItem}–{endItem} of {totalItems}
      </div>

      {/* Page navigation */}
      <div className="flex items-center gap-1">
        {/* First page */}
        <motion.button
          onClick={() => onPageChange(1)}
          disabled={currentPage === 1}
          className="p-2 rounded-lg hover:bg-white/5 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          title="First page"
          whileTap={{ scale: 0.9 }}
        >
          <ChevronsLeft className="w-4 h-4 text-text-secondary" />
        </motion.button>

        {/* Previous page */}
        <motion.button
          onClick={() => onPageChange(currentPage - 1)}
          disabled={currentPage === 1}
          className="p-2 rounded-lg hover:bg-white/5 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          title="Previous page"
          whileTap={{ scale: 0.9 }}
        >
          <ChevronLeft className="w-4 h-4 text-text-secondary" />
        </motion.button>

        {/* Page numbers */}
        <div className="flex items-center gap-1 mx-2">
          {getPageNumbers().map((page, i) =>
            page === "..." ? (
              <span key={`ellipsis-${i}`} className="px-2 text-text-muted">
                ...
              </span>
            ) : (
              <motion.button
                key={page}
                onClick={() => onPageChange(page)}
                className="relative min-w-[36px] h-9 px-3 rounded-lg text-sm font-medium transition-colors"
                whileTap={{ scale: 0.95 }}
              >
                {/* Sliding active background */}
                {currentPage === page && (
                  <motion.div
                    layoutId="pagination-active"
                    className="absolute inset-0 bg-primary-500 rounded-lg"
                    transition={layoutTransition}
                  />
                )}
                <span
                  className={`relative z-10 ${
                    currentPage === page
                      ? "text-white"
                      : "text-text-secondary hover:text-text-primary"
                  }`}
                >
                  {page}
                </span>
              </motion.button>
            )
          )}
        </div>

        {/* Next page */}
        <motion.button
          onClick={() => onPageChange(currentPage + 1)}
          disabled={currentPage === totalPages}
          className="p-2 rounded-lg hover:bg-white/5 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          title="Next page"
          whileTap={{ scale: 0.9 }}
        >
          <ChevronRight className="w-4 h-4 text-text-secondary" />
        </motion.button>

        {/* Last page */}
        <motion.button
          onClick={() => onPageChange(totalPages)}
          disabled={currentPage === totalPages}
          className="p-2 rounded-lg hover:bg-white/5 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          title="Last page"
          whileTap={{ scale: 0.9 }}
        >
          <ChevronsRight className="w-4 h-4 text-text-secondary" />
        </motion.button>
      </div>

      {/* Page size selector */}
      {showPageSizeSelector && onPageSizeChange && (
        <div className="flex items-center gap-2">
          <span className="text-sm text-text-muted">Per page:</span>
          <select
            value={itemsPerPage}
            onChange={(e) => onPageSizeChange(Number(e.target.value))}
            className="bg-bg-secondary border border-white/10 rounded-lg px-2 py-1.5 text-sm text-text-primary focus:outline-none focus:border-primary-500"
          >
            {pageSizeOptions.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </div>
      )}
    </div>
  );
}
