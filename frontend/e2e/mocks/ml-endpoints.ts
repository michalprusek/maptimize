import { Page, Route } from "@playwright/test";

/**
 * Mock responses for ML-related API endpoints.
 *
 * These mocks allow testing ML-dependent features without requiring
 * actual GPU inference. They return realistic but deterministic responses.
 */

// ============================================================================
// SAM Segmentation Mocks
// ============================================================================

/**
 * Mock SAM embedding status response.
 */
export const mockSAMEmbeddingReady = {
  image_id: 1,
  status: "ready",
  has_embedding: true,
  embedding_shape: "[1, 256, 64, 64]",
  model_variant: "mobilesam",
};

/**
 * Mock SAM embedding computing status.
 */
export const mockSAMEmbeddingComputing = {
  image_id: 1,
  status: "computing",
  has_embedding: false,
  embedding_shape: null,
  model_variant: null,
};

/**
 * Mock SAM segmentation response - returns a simple polygon.
 */
export const mockSegmentationSuccess = {
  success: true,
  polygon: [
    [100, 100],
    [200, 100],
    [200, 200],
    [100, 200],
  ] as [number, number][],
  iou_score: 0.95,
  area_pixels: 10000,
};

/**
 * Mock segmentation capabilities response.
 */
export const mockSegmentationCapabilities = {
  device: "cuda",
  variant: "mobilesam",
  supports_text_prompts: false,
  model_name: "MobileSAM",
};

// ============================================================================
// Feature Extraction / Embedding Mocks
// ============================================================================

/**
 * Mock embedding status response.
 */
export const mockEmbeddingStatus = {
  total: 100,
  with_embeddings: 95,
  without_embeddings: 5,
  percentage: 95.0,
};

/**
 * Mock UMAP data response.
 */
export const mockUmapData = {
  points: [
    {
      crop_id: 1,
      image_id: 1,
      experiment_id: 1,
      x: 0.5,
      y: 0.3,
      protein_name: "MAP2",
      protein_color: "#4F46E5",
      thumbnail_url: "/api/images/crops/1/image",
      bundleness_score: 0.8,
    },
    {
      crop_id: 2,
      image_id: 1,
      experiment_id: 1,
      x: -0.2,
      y: 0.7,
      protein_name: "Tau",
      protein_color: "#10B981",
      thumbnail_url: "/api/images/crops/2/image",
      bundleness_score: 0.6,
    },
  ],
  total_crops: 100,
  n_neighbors: 15,
  min_dist: 0.1,
  silhouette_score: 0.65,
};

// ============================================================================
// Detection Mocks
// ============================================================================

/**
 * Mock batch process response.
 */
export const mockBatchProcessSuccess = {
  processing_count: 5,
  message: "Processing started for 5 images",
};

/**
 * Mock image processing status.
 */
export const mockImageReady = {
  id: 1,
  experiment_id: 1,
  original_filename: "test-image.tif",
  status: "READY",
  width: 512,
  height: 512,
  z_slices: 10,
  file_size: 1024000,
  cell_count: 15,
};

// ============================================================================
// Mock Setup Functions
// ============================================================================

/**
 * Set up all ML endpoint mocks for a page.
 *
 * @param page - Playwright page instance
 * @param options - Configuration options for mocks
 */
export async function mockMLEndpoints(
  page: Page,
  options: {
    segmentationReady?: boolean;
    embeddingsComplete?: boolean;
    failSegmentation?: boolean;
  } = {}
): Promise<void> {
  const {
    segmentationReady = true,
    embeddingsComplete = true,
    failSegmentation = false,
  } = options;

  // SAM Embedding Status
  await page.route("**/api/segmentation/embedding-status/*", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        segmentationReady ? mockSAMEmbeddingReady : mockSAMEmbeddingComputing
      ),
    });
  });

  // SAM Compute Embedding
  await page.route("**/api/segmentation/compute-embedding/*", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ message: "Embedding computation started", image_id: 1 }),
    });
  });

  // SAM Interactive Segmentation
  await page.route("**/api/segmentation/segment", async (route: Route) => {
    if (failSegmentation) {
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ success: false, error: "Segmentation failed" }),
      });
    } else {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(mockSegmentationSuccess),
      });
    }
  });

  // Segmentation Capabilities
  await page.route("**/api/segmentation/capabilities", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(mockSegmentationCapabilities),
    });
  });

  // Save Segmentation Mask
  await page.route("**/api/segmentation/save-mask", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ success: true, crop_id: 1, area_pixels: 10000 }),
    });
  });

  // Embedding Status
  await page.route("**/api/embeddings/status*", async (route: Route) => {
    const response = embeddingsComplete
      ? mockEmbeddingStatus
      : { ...mockEmbeddingStatus, without_embeddings: 50, percentage: 50.0 };
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(response),
    });
  });

  // UMAP Data
  await page.route("**/api/embeddings/umap*", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(mockUmapData),
    });
  });

  // Feature Extraction Trigger
  await page.route("**/api/embeddings/extract*", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ message: "Feature extraction started", pending: 10 }),
    });
  });
}

/**
 * Mock image upload and processing endpoints.
 */
export async function mockImageProcessing(page: Page): Promise<void> {
  // Batch Process
  await page.route("**/api/images/batch-process", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(mockBatchProcessSuccess),
    });
  });

  // Batch Redetect
  await page.route("**/api/images/batch-redetect", async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ processed_count: 5, message: "Re-detection complete" }),
    });
  });
}

/**
 * Mock a slow API response for testing loading states.
 */
export async function mockSlowResponse(
  page: Page,
  urlPattern: string | RegExp,
  delayMs: number = 2000
): Promise<void> {
  await page.route(urlPattern, async (route: Route) => {
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    await route.continue();
  });
}

/**
 * Mock an API error response.
 */
export async function mockApiError(
  page: Page,
  urlPattern: string | RegExp,
  status: number = 500,
  message: string = "Internal server error"
): Promise<void> {
  await page.route(urlPattern, async (route: Route) => {
    await route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify({ detail: message }),
    });
  });
}

/**
 * Clear all route mocks.
 */
export async function clearMocks(page: Page): Promise<void> {
  await page.unrouteAll();
}

// ============================================================================
// Ranking Page Mocks
// ============================================================================

/** Default mock data for ranking pair response */
export const mockRankingPair = {
  crop_a: { id: 1, image_id: 1, mip_url: "/api/images/crops/1/image" },
  crop_b: { id: 2, image_id: 1, mip_url: "/api/images/crops/2/image" },
  comparison_number: 1,
  total_comparisons: 100,
};

/** Mock comparison result */
export const mockCompareResult = {
  id: 1,
  crop_a_id: 1,
  crop_b_id: 2,
  winner_id: 1,
  timestamp: new Date().toISOString(),
};

/** Type for ranking pair data that can have null crops */
export type RankingPairData = {
  crop_a: { id: number; image_id: number; mip_url: string } | null;
  crop_b: { id: number; image_id: number; mip_url: string } | null;
  comparison_number: number;
  total_comparisons: number;
};

/**
 * Set up ranking page mocks for pairwise comparison tests.
 * Reduces boilerplate in ranking tests.
 */
export async function mockRankingEndpoints(
  page: Page,
  options: {
    pairData?: RankingPairData;
    winnerId?: number;
  } = {}
): Promise<void> {
  const { pairData = mockRankingPair, winnerId = 1 } = options;

  // Mock ranking pair endpoint
  await page.route("**/api/ranking/pair*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(pairData),
    });
  });

  // Mock compare endpoint
  await page.route("**/api/ranking/compare", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...mockCompareResult,
        winner_id: winnerId,
      }),
    });
  });

  // Mock undo endpoint
  await page.route("**/api/ranking/undo", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(mockCompareResult),
    });
  });
}

// ============================================================================
// Editor Page Mocks
// ============================================================================

/** Default SVG placeholder for image mocks */
const DEFAULT_SVG_PLACEHOLDER = `<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512">
  <rect width="512" height="512" fill="#1a1a2e"/>
</svg>`;

export interface EditorMockOptions {
  /** List of FOV images to mock. Default: single image with id 1 */
  fovImages?: Array<{ id: number; experiment_id: number; original_filename: string; status: string }>;
  /** Cell crops for the FOV. Default: empty array */
  crops?: unknown[];
  /** Custom SVG content for image file. Default: simple colored rectangle */
  svgContent?: string;
}

/**
 * Set up all endpoints required by the Editor page.
 * This eliminates repetitive mock setup in editor tests.
 *
 * @param page - Playwright page instance
 * @param options - Configuration for the mocks
 */
export async function mockEditorEndpoints(
  page: Page,
  options: EditorMockOptions = {}
): Promise<void> {
  const {
    fovImages = [{ id: 1, experiment_id: 1, original_filename: "test.tif", status: "READY" }],
    crops = [],
    svgContent = DEFAULT_SVG_PLACEHOLDER,
  } = options;

  // Mock FOV list endpoint
  await page.route("**/api/images/fovs*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(fovImages),
    });
  });

  // Mock crops endpoint (required by editor page)
  await page.route("**/api/images/*/crops*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(crops),
    });
  });

  // Mock image file endpoint
  await page.route("**/api/images/*/file*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "image/svg+xml",
      body: svgContent,
    });
  });

  // Mock single image data endpoint (handles /api/images/{id} without additional path segments)
  await page.route(/\/api\/images\/\d+$/, async (route) => {
    const url = route.request().url();
    const idMatch = url.match(/images\/(\d+)/);
    const id = idMatch ? parseInt(idMatch[1], 10) : 1;

    // Find the matching FOV or create a default response
    const fov = fovImages.find(f => f.id === id) || {
      id,
      experiment_id: 1,
      original_filename: `image-${id}.tif`,
      status: "READY",
    };

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...fov,
        width: 512,
        height: 512,
      }),
    });
  });
}
