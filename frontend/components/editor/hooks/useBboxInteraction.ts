"use client";

/**
 * useBboxInteraction Hook
 *
 * State machine for bbox drag, resize, and draw interactions.
 * Handles mouse events and converts them to bbox operations.
 */

import { useState, useCallback, useRef, type RefObject, type MouseEvent } from "react";
import type { EditorBbox, HandlePosition, Point, EditorState, Rect } from "@/lib/editor/types";
import {
  getHandleAtPosition,
  findBboxAtPosition,
  resizeBbox,
  moveBbox,
  createBboxFromCorners,
  getCursorForHandle,
} from "@/lib/editor/geometry";
import { MIN_BBOX_SIZE } from "@/lib/editor/constants";

type InteractionState =
  | { type: "idle" }
  | { type: "hovering"; bboxId: string | number; handle: HandlePosition | null }
  | { type: "dragging"; bboxId: string | number; startPos: Point; originalBbox: Rect }
  | { type: "resizing"; bboxId: string | number; handle: HandlePosition; startPos: Point; originalBbox: Rect }
  | { type: "drawing"; startPos: Point; currentPos: Point }
  | { type: "panning"; startPos: Point; originalPan: Point };

export interface UseBboxInteractionOptions {
  bboxes: EditorBbox[];
  editorState: EditorState;
  setEditorState: React.Dispatch<React.SetStateAction<EditorState>>;
  canvasRef: RefObject<HTMLElement | null>;
  imageWidth: number;
  imageHeight: number;
  /** Called during drag/resize - local state update only */
  onBboxChange: (id: string | number, changes: Partial<EditorBbox>) => void;
  /** Called when drag/resize completes - for API save and undo */
  onBboxChangeComplete: (id: string | number, originalBbox: Rect, finalBbox: Rect) => void;
  onBboxCreate: (bbox: Rect) => void;
  onBboxSelect: (id: string | number | null) => void;
}

export interface UseBboxInteractionReturn {
  handleMouseDown: (e: MouseEvent<HTMLElement>) => void;
  handleMouseMove: (e: MouseEvent<HTMLElement>) => void;
  handleMouseUp: (e: MouseEvent<HTMLElement>) => void;
  handleMouseLeave: () => void;
  handleWheel: (e: WheelEvent) => void;
  cursor: string;
  drawingBbox: Rect | null;
  /** True when a bbox is being dragged or resized */
  isModifyingBbox: boolean;
  /** ID of the bbox currently being modified, or null */
  modifyingBboxId: string | number | null;
  /** Live bbox rect during drag/resize (updated every mouse move, not from state) */
  liveBboxRect: Rect | null;
}

/**
 * Get mouse position relative to the container element.
 */
function getCanvasPosition(
  e: MouseEvent<HTMLElement>,
  container: HTMLElement | null
): Point {
  if (!container) return { x: 0, y: 0 };
  const rect = container.getBoundingClientRect();
  return {
    x: e.clientX - rect.left,
    y: e.clientY - rect.top,
  };
}

export function useBboxInteraction(
  options: UseBboxInteractionOptions
): UseBboxInteractionReturn {
  const {
    bboxes,
    editorState,
    setEditorState,
    canvasRef,
    imageWidth,
    imageHeight,
    onBboxChange,
    onBboxChangeComplete,
    onBboxCreate,
    onBboxSelect,
  } = options;

  const [interactionState, setInteractionState] = useState<InteractionState>({ type: "idle" });
  const [cursor, setCursor] = useState("default");
  const [drawingBbox, setDrawingBbox] = useState<Rect | null>(null);
  const [liveBboxRect, setLiveBboxRect] = useState<Rect | null>(null);

  // Use ref for interaction state to avoid stale closures
  const interactionRef = useRef(interactionState);
  interactionRef.current = interactionState;

  const handleMouseDown = useCallback(
    (e: MouseEvent<HTMLElement>) => {
      const canvas = canvasRef.current;
      if (!canvas) return;

      const pos = getCanvasPosition(e, canvas);
      const { zoom, panOffset, isSpacePressed, mode } = editorState;

      // Middle mouse button or space pressed = start panning
      if (e.button === 1 || isSpacePressed) {
        setInteractionState({
          type: "panning",
          startPos: pos,
          originalPan: { ...panOffset },
        });
        setCursor("grabbing");
        return;
      }

      // Right click is handled by context menu, skip here
      if (e.button === 2) return;

      // Adjust position for pan and zoom
      const adjustedPos = {
        x: (pos.x - panOffset.x) / zoom,
        y: (pos.y - panOffset.y) / zoom,
      };

      // Check if clicking on a handle of selected bbox
      if (editorState.selectedBboxId !== null) {
        const selectedBbox = bboxes.find((b) => b.id === editorState.selectedBboxId);
        if (selectedBbox) {
          const handle = getHandleAtPosition(
            { x: pos.x - panOffset.x, y: pos.y - panOffset.y },
            selectedBbox,
            zoom
          );
          if (handle) {
            setInteractionState({
              type: "resizing",
              bboxId: selectedBbox.id,
              handle,
              startPos: pos,
              originalBbox: {
                x: selectedBbox.x,
                y: selectedBbox.y,
                width: selectedBbox.width,
                height: selectedBbox.height,
              },
            });
            return;
          }
        }
      }

      const adjustedPosScaled = {
        x: pos.x - panOffset.x,
        y: pos.y - panOffset.y,
      };

      // In draw mode: always start drawing new bbox, don't select existing ones
      if (mode === "draw") {
        onBboxSelect(null);
        setInteractionState({
          type: "drawing",
          startPos: adjustedPosScaled,
          currentPos: adjustedPosScaled,
        });
        setCursor("crosshair");
        return;
      }

      // In view mode: check if clicking inside any bbox to select/drag it
      const clickedBbox = findBboxAtPosition(adjustedPosScaled, bboxes, zoom);

      if (clickedBbox) {
        onBboxSelect(clickedBbox.id);
        setInteractionState({
          type: "dragging",
          bboxId: clickedBbox.id,
          startPos: pos,
          originalBbox: {
            x: clickedBbox.x,
            y: clickedBbox.y,
            width: clickedBbox.width,
            height: clickedBbox.height,
          },
        });
        setCursor("move");
        return;
      }

      // Click on empty space = start panning (left mouse button drag)
      onBboxSelect(null);
      setInteractionState({
        type: "panning",
        startPos: pos,
        originalPan: { ...panOffset },
      });
      setCursor("grab");
    },
    [bboxes, editorState, canvasRef, onBboxSelect]
  );

  const handleMouseMove = useCallback(
    (e: MouseEvent<HTMLElement>) => {
      const canvas = canvasRef.current;
      if (!canvas) return;

      const pos = getCanvasPosition(e, canvas);
      const { zoom, panOffset, selectedBboxId } = editorState;
      const interaction = interactionRef.current;

      // Handle panning
      if (interaction.type === "panning") {
        const dx = pos.x - interaction.startPos.x;
        const dy = pos.y - interaction.startPos.y;
        setEditorState((prev) => ({
          ...prev,
          panOffset: {
            x: interaction.originalPan.x + dx,
            y: interaction.originalPan.y + dy,
          },
        }));
        return;
      }

      // Handle resizing
      if (interaction.type === "resizing") {
        const delta = {
          x: pos.x - interaction.startPos.x,
          y: pos.y - interaction.startPos.y,
        };
        const newRect = resizeBbox(
          interaction.originalBbox,
          interaction.handle,
          delta,
          zoom,
          imageWidth,
          imageHeight
        );
        // Update live rect immediately (no React state delay)
        setLiveBboxRect(newRect);
        onBboxChange(interaction.bboxId, {
          x: newRect.x,
          y: newRect.y,
          width: newRect.width,
          height: newRect.height,
          isModified: true,
        });
        return;
      }

      // Handle dragging
      if (interaction.type === "dragging") {
        const delta = {
          x: pos.x - interaction.startPos.x,
          y: pos.y - interaction.startPos.y,
        };
        const newRect = moveBbox(
          interaction.originalBbox,
          delta,
          zoom,
          imageWidth,
          imageHeight
        );
        // Update live rect immediately (no React state delay)
        setLiveBboxRect({
          ...newRect,
          width: interaction.originalBbox.width,
          height: interaction.originalBbox.height,
        });
        onBboxChange(interaction.bboxId, {
          x: newRect.x,
          y: newRect.y,
          isModified: true,
        });
        return;
      }

      // Handle drawing
      if (interaction.type === "drawing") {
        const adjustedPos = {
          x: pos.x - panOffset.x,
          y: pos.y - panOffset.y,
        };
        setInteractionState({
          ...interaction,
          currentPos: adjustedPos,
        });

        // Update drawing preview
        const bbox = createBboxFromCorners(
          interaction.startPos,
          adjustedPos,
          zoom,
          imageWidth,
          imageHeight
        );
        setDrawingBbox(bbox);
        return;
      }

      // Not in any interaction - update hover state
      const adjustedPos = {
        x: pos.x - panOffset.x,
        y: pos.y - panOffset.y,
      };

      // Check for handle hover on selected bbox
      if (selectedBboxId !== null) {
        const selectedBbox = bboxes.find((b) => b.id === selectedBboxId);
        if (selectedBbox) {
          const handle = getHandleAtPosition(adjustedPos, selectedBbox, zoom);
          if (handle) {
            setCursor(getCursorForHandle(handle));
            setEditorState((prev) => ({
              ...prev,
              hoveredBboxId: selectedBboxId,
            }));
            return;
          }
        }
      }

      // Check for bbox hover (only in view mode, not in draw mode)
      if (editorState.mode !== "draw") {
        const hoveredBbox = findBboxAtPosition(adjustedPos, bboxes, zoom);
        if (hoveredBbox) {
          setCursor("move");
          setEditorState((prev) => ({
            ...prev,
            hoveredBboxId: hoveredBbox.id,
          }));
          return;
        }
      }

      // No bbox hovered or in draw mode
      setCursor(editorState.mode === "draw" ? "crosshair" : "grab");
      setEditorState((prev) => ({
        ...prev,
        hoveredBboxId: null,
      }));
    },
    [bboxes, editorState, canvasRef, imageWidth, imageHeight, onBboxChange, setEditorState]
  );

  const handleMouseUp = useCallback(
    (e: MouseEvent<HTMLElement>) => {
      const interaction = interactionRef.current;

      // Finish drawing
      if (interaction.type === "drawing" && drawingBbox) {
        if (drawingBbox.width >= MIN_BBOX_SIZE && drawingBbox.height >= MIN_BBOX_SIZE) {
          onBboxCreate(drawingBbox);
        }
        setDrawingBbox(null);
      }

      // Finish dragging or resizing - notify completion with original and final positions
      if (interaction.type === "dragging" || interaction.type === "resizing") {
        const bboxId = interaction.bboxId;
        const originalBbox = interaction.originalBbox;
        const currentBbox = bboxes.find((b) => b.id === bboxId);

        if (currentBbox) {
          const finalBbox: Rect = {
            x: currentBbox.x,
            y: currentBbox.y,
            width: currentBbox.width,
            height: currentBbox.height,
          };

          // Only call complete if position actually changed
          if (
            originalBbox.x !== finalBbox.x ||
            originalBbox.y !== finalBbox.y ||
            originalBbox.width !== finalBbox.width ||
            originalBbox.height !== finalBbox.height
          ) {
            onBboxChangeComplete(bboxId, originalBbox, finalBbox);
          }
        }
      }

      // Clear live bbox rect
      setLiveBboxRect(null);

      // Reset interaction state
      setInteractionState({ type: "idle" });
      setCursor(editorState.mode === "draw" ? "crosshair" : "grab");
    },
    [bboxes, drawingBbox, editorState.mode, onBboxCreate, onBboxChangeComplete]
  );

  const handleMouseLeave = useCallback(() => {
    // Cancel any in-progress interaction
    if (interactionRef.current.type === "drawing") {
      setDrawingBbox(null);
    }
    setLiveBboxRect(null);
    setInteractionState({ type: "idle" });
    setCursor(editorState.mode === "draw" ? "crosshair" : "grab");
    setEditorState((prev) => ({
      ...prev,
      hoveredBboxId: null,
    }));
  }, [setEditorState, editorState.mode]);

  const handleWheel = useCallback(
    (e: WheelEvent) => {
      e.preventDefault();
      const canvas = canvasRef.current;
      if (!canvas) return;

      const rect = canvas.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;

      setEditorState((prev) => {
        const zoomDelta = e.deltaY > 0 ? -0.1 : 0.1;
        const newZoom = Math.min(Math.max(prev.zoom + zoomDelta, 0.1), 10);

        // Zoom toward mouse position
        const zoomRatio = newZoom / prev.zoom;
        const newPanX = mouseX - (mouseX - prev.panOffset.x) * zoomRatio;
        const newPanY = mouseY - (mouseY - prev.panOffset.y) * zoomRatio;

        return {
          ...prev,
          zoom: newZoom,
          panOffset: { x: newPanX, y: newPanY },
        };
      });
    },
    [canvasRef, setEditorState]
  );

  // Compute modification state using pattern matching (avoids type assertion)
  const isModifyingBbox =
    interactionState.type === "dragging" || interactionState.type === "resizing";
  const modifyingBboxId =
    interactionState.type === "dragging" || interactionState.type === "resizing"
      ? interactionState.bboxId
      : null;

  return {
    handleMouseDown,
    handleMouseMove,
    handleMouseUp,
    handleMouseLeave,
    handleWheel,
    cursor,
    drawingBbox,
    isModifyingBbox,
    modifyingBboxId,
    liveBboxRect,
  };
}
