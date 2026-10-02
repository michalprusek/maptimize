"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";

import { ColorTagSelect, toColorTagOptions } from "@/components/ui/ColorTagSelect";
import type { ColorTagOption } from "@/components/ui/ColorTagSelect";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import { useCreateCellLine, useDeleteCellLine } from "@/hooks";
import { api } from "@/lib/api";
import { cellLineDeleteVerdict } from "./cellLineDelete";

interface CellLinePickerProps {
  value: number | null;
  onChange: (cellLineId: number | null) => void;
  /** Surfaced through the caller's own banner; this component has no error UI. */
  onError: (message: string) => void;
  /** Cleared on success, so a stale banner does not outlive the thing it described. */
  onErrorCleared?: () => void;
  variant?: "field" | "chip";
  size?: "sm" | "md";
  align?: "left" | "right";
  showHint?: boolean;
  /** Needed by callers inside a card, which must lift it while the menu is open. */
  onOpenChange?: (open: boolean) => void;
  disabled?: boolean;
}

/**
 * The cell-line picker: choose one, type a new one, or delete an unused one.
 *
 * One component rather than the props spelled out at each site, because there
 * are three sites (the experiment card chip, the create-experiment form, the
 * experiment detail chip) and they were already repeating the options query,
 * the create wiring and three i18n keys. Delete would have made it a fourth
 * repetition — and a `ConfirmModal` copied three times is three places for the
 * confirmation copy to drift.
 *
 * ⚠️ Cell lines are shared reference data with NO admin page: creating one here
 * and deleting one here are the only ways either happens in the product. That
 * is why the delete is guarded twice — `cellLineDeleteVerdict` disables the
 * button before it can be clicked, and the backend still answers 409 for the
 * API and MCP paths that never see this component.
 */
export function CellLinePicker({
  value,
  onChange,
  onError,
  onErrorCleared,
  variant = "field",
  size,
  align,
  showHint = false,
  onOpenChange,
  disabled,
}: CellLinePickerProps): JSX.Element {
  const t = useTranslations("experiments");
  const tCommon = useTranslations("common");
  const [toDelete, setToDelete] = useState<ColorTagOption | null>(null);

  const { data: cellLines } = useQuery({
    queryKey: ["cellLines"],
    queryFn: () => api.getCellLines(),
  });

  const createCellLine = useCreateCellLine({
    fallbackMessage: t("createCellLineError"),
    onError,
    onSuccess: onErrorCleared,
  });

  const deleteMutation = useDeleteCellLine({
    fallbackMessage: t("deleteCellLineError"),
    onError: (message) => {
      onError(message);
      setToDelete(null);
    },
    onSuccess: () => {
      onErrorCleared?.();
      setToDelete(null);
    },
  });

  return (
    <>
      <ColorTagSelect
        options={toColorTagOptions(cellLines, (line) => line.description)}
        value={value}
        onChange={onChange}
        onOpenChange={onOpenChange}
        disabled={disabled}
        create={{
          onCreate: createCellLine,
          searchPlaceholder: t("searchOrTypeCellLine"),
          label: (name) => t("createCellLine", { name }),
        }}
        remove={{
          blockedReason: (option) => {
            const verdict = cellLineDeleteVerdict(cellLines, option.id);
            if (verdict.kind === "allowed") return null;
            // "Unknown" gets the same sentence as "in use" with a count of
            // zero rather than a second message: both mean "not now", and the
            // only case that reaches it is a list still loading.
            return verdict.kind === "in-use"
              ? t("deleteCellLineInUse", { count: verdict.experiments })
              : tCommon("loading");
          },
          onRemove: setToDelete,
          label: () => t("deleteCellLine"),
        }}
        placeholder={t("unassignedCellLine")}
        clearLabel={t("unassignedCellLine")}
        hint={showHint ? t("cellLineHint") : undefined}
        variant={variant}
        size={size}
        align={align}
      />

      <ConfirmModal
        isOpen={toDelete !== null}
        onClose={() => setToDelete(null)}
        onConfirm={() => toDelete && deleteMutation.mutate(toDelete.id)}
        title={t("deleteCellLine")}
        message={t("deleteCellLineConfirm")}
        detail={toDelete?.name}
        confirmLabel={tCommon("delete")}
        cancelLabel={tCommon("cancel")}
        isLoading={deleteMutation.isPending}
        variant="danger"
      />
    </>
  );
}
