/**
 * API client for MAPtimize backend
 */

export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface ApiError {
  detail: string;
}

class ApiClient {
  private token: string | null = null;

  setToken(token: string | null) {
    this.token = token;
    if (token) {
      localStorage.setItem("token", token);
    } else {
      localStorage.removeItem("token");
      this.token = null;
    }
  }

  getToken(): string | null {
    if (typeof window === "undefined") return null;
    // Always sync with localStorage to avoid stale tokens
    const storedToken = localStorage.getItem("token");
    if (storedToken !== this.token) {
      this.token = storedToken;
    }
    return this.token;
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${API_URL}${endpoint}`;
    const token = this.getToken();

    const headers: Record<string, string> = {
      ...(options.headers as Record<string, string>),
    };

    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }

    // Only set JSON content type if not already set and not FormData
    const existingContentType = headers["Content-Type"];
    if (!(options.body instanceof FormData) && !existingContentType) {
      headers["Content-Type"] = "application/json";
    }

    let response: Response;
    try {
      response = await fetch(url, {
        ...options,
        headers,
      });
    } catch (networkError) {
      console.error(`[API] Network error calling ${endpoint}:`, networkError);
      if (networkError instanceof TypeError && networkError.message.includes("fetch")) {
        throw new Error("Unable to connect to the server. Please check your internet connection.");
      }
      throw new Error(`Network error: ${networkError instanceof Error ? networkError.message : "Unknown error"}`);
    }

    if (!response.ok) {
      let errorDetail: string;
      try {
        const error: ApiError = await response.json();
        errorDetail = error.detail;
      } catch {
        // Response wasn't JSON - log for debugging
        let rawBody = "";
        try {
          rawBody = await response.text();
        } catch {
          rawBody = "<unable to read response>";
        }
        console.error(
          `[API] Non-JSON error from ${endpoint}:`,
          `Status: ${response.status}`,
          `Body: ${rawBody.substring(0, 500)}`
        );

        // Provide user-friendly messages for common errors
        if (response.status === 502 || response.status === 503 || response.status === 504) {
          errorDetail = "Server is temporarily unavailable. Please try again.";
        } else if (response.status === 401) {
          errorDetail = "Your session has expired. Please log in again.";
        } else {
          errorDetail = `Request failed: ${response.status} ${response.statusText}`;
        }
      }
      throw new Error(errorDetail);
    }

    if (response.status === 204) {
      return {} as T;
    }

    return response.json();
  }

  // Auth endpoints
  async register(data: { email: string; name: string; password: string }) {
    return this.request<{ access_token: string; user: User }>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async login(email: string, password: string) {
    const formData = new URLSearchParams();
    formData.append("username", email);
    formData.append("password", password);

    return this.request<{ access_token: string; user: User }>("/api/auth/login", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: formData,
    });
  }

  async getMe() {
    return this.request<User>("/api/auth/me");
  }

  // Experiments
  async getExperiments() {
    return this.request<Experiment[]>("/api/experiments");
  }

  async createExperiment(data: { name: string; description?: string }) {
    return this.request<Experiment>("/api/experiments", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async getExperiment(id: number) {
    return this.request<Experiment>(`/api/experiments/${id}`);
  }

  async deleteExperiment(id: number) {
    return this.request<void>(`/api/experiments/${id}`, {
      method: "DELETE",
    });
  }

  // Images

  /**
   * Upload a microscopy image (Phase 1 of two-phase workflow).
   * This creates projections and thumbnail. Use batchProcessImages for Phase 2.
   */
  async uploadImage(
    experimentId: number,
    file: File,
    mapProteinId?: number
  ) {
    const formData = new FormData();
    formData.append("experiment_id", experimentId.toString());
    formData.append("file", file);
    if (mapProteinId) {
      formData.append("map_protein_id", mapProteinId.toString());
    }

    return this.request<Image>("/api/images/upload", {
      method: "POST",
      body: formData,
    });
  }

  /**
   * Start Phase 2 processing for multiple images (batch processing).
   * Configures detection settings and starts detection + feature extraction.
   */
  async batchProcessImages(
    imageIds: number[],
    detectCells: boolean = true,
    mapProteinId?: number
  ) {
    const body: { image_ids: number[]; detect_cells: boolean; map_protein_id?: number } = {
      image_ids: imageIds,
      detect_cells: detectCells,
    };
    if (mapProteinId !== undefined) {
      body.map_protein_id = mapProteinId;
    }
    return this.request<BatchProcessResponse>("/api/images/batch-process", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  /**
   * Get FOV (Field of View) images for an experiment.
   * Returns Image records for the FOV gallery view.
   */
  async getFOVs(experimentId: number) {
    return this.request<FOVImage[]>(`/api/images/fovs?experiment_id=${experimentId}`);
  }

  async getImages(experimentId: number) {
    return this.request<Image[]>(`/api/images?experiment_id=${experimentId}`);
  }

  async getImage(id: number) {
    return this.request<Image>(`/api/images/${id}`);
  }

  /**
   * Get a single FOV image by ID.
   */
  async getFovImage(fovId: number) {
    return this.request<FOVImage>(`/api/images/${fovId}`);
  }

  /**
   * Get all cell crops for a specific FOV image.
   */
  async getFovCrops(fovId: number, excludeExcluded = false) {
    return this.request<CellCropGallery[]>(
      `/api/images/${fovId}/crops?exclude_excluded=${excludeExcluded}`
    );
  }

  getImageUrl(imageId: number, type: "original" | "mip" | "thumbnail" = "mip") {
    const token = this.getToken();
    if (!token) {
      console.warn(`[API] No token available for image ${imageId}`);
    }
    return `${API_URL}/api/images/${imageId}/file?type=${type}&token=${token}`;
  }

  async deleteImage(id: number) {
    return this.request<void>(`/api/images/${id}`, {
      method: "DELETE",
    });
  }

  // Proteins
  async getProteins() {
    return this.request<MapProtein[]>("/api/proteins");
  }

  async createProtein(data: { name: string; full_name?: string; color?: string }) {
    return this.request<MapProtein>("/api/proteins", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  // Ranking
  async getRankingPair(experimentId?: number) {
    const params = experimentId ? `?experiment_id=${experimentId}` : "";
    return this.request<PairResponse>(`/api/ranking/pair${params}`);
  }

  async submitComparison(data: {
    crop_a_id: number;
    crop_b_id: number;
    winner_id: number;
    response_time_ms?: number;
  }) {
    return this.request<Comparison>("/api/ranking/compare", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async undoComparison() {
    return this.request<Comparison>("/api/ranking/undo", {
      method: "POST",
    });
  }

  async getLeaderboard(experimentId?: number, page = 1, perPage = 500) {
    const params = new URLSearchParams({
      page: page.toString(),
      per_page: perPage.toString(),
    });
    if (experimentId) params.append("experiment_id", experimentId.toString());
    return this.request<RankingResponse>(`/api/ranking/leaderboard?${params}`);
  }

  async getRankingProgress(experimentId?: number) {
    const params = experimentId ? `?experiment_id=${experimentId}` : "";
    return this.request<ProgressResponse>(`/api/ranking/progress${params}`);
  }

  // Metrics
  async getMetrics() {
    return this.request<MetricListResponse>("/api/metrics");
  }

  async createMetric(data: { name: string; description?: string }) {
    return this.request<Metric>("/api/metrics", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async getMetric(id: number) {
    return this.request<Metric>(`/api/metrics/${id}`);
  }

  async deleteMetric(id: number) {
    return this.request<void>(`/api/metrics/${id}`, {
      method: "DELETE",
    });
  }

  async getMetricImages(metricId: number) {
    return this.request<MetricImage[]>(`/api/metrics/${metricId}/images`);
  }

  async importCropsToMetric(metricId: number, experimentIds: number[]) {
    return this.request<{ imported_count: number; skipped_count: number }>(
      `/api/metrics/${metricId}/images/import`,
      {
        method: "POST",
        body: JSON.stringify({ experiment_ids: experimentIds }),
      }
    );
  }

  async getExperimentsForImport(metricId: number) {
    return this.request<ExperimentForImport[]>(`/api/metrics/${metricId}/experiments`);
  }

  async deleteMetricImage(metricId: number, imageId: number) {
    return this.request<void>(`/api/metrics/${metricId}/images/${imageId}`, {
      method: "DELETE",
    });
  }

  async getMetricPair(metricId: number) {
    return this.request<MetricPairResponse>(`/api/metrics/${metricId}/pair`);
  }

  async submitMetricComparison(
    metricId: number,
    data: {
      image_a_id: number;
      image_b_id: number;
      winner_id: number;
      response_time_ms?: number;
    }
  ) {
    return this.request<MetricComparison>(`/api/metrics/${metricId}/compare`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async undoMetricComparison(metricId: number) {
    return this.request<MetricComparison>(`/api/metrics/${metricId}/undo`, {
      method: "POST",
    });
  }

  async getMetricLeaderboard(metricId: number, page = 1, perPage = 500) {
    const params = new URLSearchParams({
      page: page.toString(),
      per_page: perPage.toString(),
    });
    return this.request<MetricRankingResponse>(`/api/metrics/${metricId}/leaderboard?${params}`);
  }

  async getMetricProgress(metricId: number) {
    return this.request<MetricProgressResponse>(`/api/metrics/${metricId}/progress`);
  }

  getMetricImageUrl(metricId: number, imageId: number) {
    const token = this.getToken();
    if (!token) {
      console.warn(`[API] No token available for metric image ${metricId}/${imageId}`);
    }
    return `${API_URL}/api/metrics/${metricId}/images/${imageId}/file?token=${token}`;
  }

  getCropImageUrl(cropId: number, type: "mip" | "sum" = "mip") {
    const token = this.getToken();
    if (!token) {
      console.warn(`[API] No token available for crop ${cropId}`);
    }
    return `${API_URL}/api/images/crops/${cropId}/image?type=${type}&token=${token}`;
  }

  async getCellCrops(experimentId: number, excludeExcluded = true) {
    return this.request<CellCropGallery[]>(
      `/api/images/crops?experiment_id=${experimentId}&exclude_excluded=${excludeExcluded}`
    );
  }

  async deleteCellCrop(cropId: number) {
    return this.request<void>(`/api/images/crops/${cropId}`, {
      method: "DELETE",
    });
  }

  async updateCellCropProtein(cropId: number, mapProteinId: number | null) {
    const params = new URLSearchParams();
    if (mapProteinId !== null) {
      params.set("map_protein_id", mapProteinId.toString());
    }
    return this.request<{
      id: number;
      map_protein_id: number | null;
      map_protein_name: string | null;
      map_protein_color: string | null;
    }>(`/api/images/crops/${cropId}/protein?${params.toString()}`, {
      method: "PATCH",
    });
  }

  // Crop Editor API methods

  /**
   * Update bounding box coordinates for a cell crop.
   * Use regenerateCropFeatures after to regenerate crop images and features.
   */
  async updateCropBbox(cropId: number, bbox: { x: number; y: number; width: number; height: number }) {
    return this.request<CropBboxUpdateResponse>(`/api/images/crops/${cropId}/bbox`, {
      method: "PATCH",
      body: JSON.stringify({
        bbox_x: bbox.x,
        bbox_y: bbox.y,
        bbox_w: bbox.width,
        bbox_h: bbox.height,
      }),
    });
  }

  /**
   * Regenerate crop images and features from current bbox coordinates.
   */
  async regenerateCropFeatures(cropId: number) {
    return this.request<CropRegenerateResponse>(`/api/images/crops/${cropId}/regenerate`, {
      method: "POST",
      body: JSON.stringify({ async_processing: false }),
    });
  }

  /**
   * Create a new manual crop on an FOV image.
   */
  async createManualCrop(
    fovId: number,
    bbox: { x: number; y: number; width: number; height: number },
    mapProteinId?: number
  ) {
    return this.request<ManualCropCreateResponse>(`/api/images/${fovId}/crops`, {
      method: "POST",
      body: JSON.stringify({
        bbox_x: bbox.x,
        bbox_y: bbox.y,
        bbox_w: bbox.width,
        bbox_h: bbox.height,
        map_protein_id: mapProteinId,
      }),
    });
  }

  /**
   * Apply batch changes to crops (create, update, delete).
   */
  async batchUpdateCrops(
    fovId: number,
    changes: CropBatchUpdateItem[],
    regenerateFeatures = true,
    confirmDeleteComparisons = false
  ) {
    return this.request<CropBatchUpdateResponse>(`/api/images/${fovId}/crops/batch`, {
      method: "PATCH",
      body: JSON.stringify({
        changes,
        regenerate_features: regenerateFeatures,
        confirm_delete_comparisons: confirmDeleteComparisons,
      }),
    });
  }

  // Embeddings / UMAP
  async getUmapData(
    experimentId?: number,
    umapType: UmapType = "cropped",
    nNeighbors = 15,
    minDist = 0.1
  ): Promise<UmapDataResponse | UmapFovDataResponse> {
    const params = new URLSearchParams({
      umap_type: umapType,
      n_neighbors: nNeighbors.toString(),
      min_dist: minDist.toString(),
    });
    if (experimentId) {
      params.append("experiment_id", experimentId.toString());
    }
    if (umapType === "fov") {
      return this.request<UmapFovDataResponse>(`/api/embeddings/umap?${params}`);
    }
    return this.request<UmapDataResponse>(`/api/embeddings/umap?${params}`);
  }

  async triggerUmapRecomputation(umapType: UmapType, experimentId?: number) {
    const params = new URLSearchParams({ umap_type: umapType });
    if (experimentId) {
      params.append("experiment_id", experimentId.toString());
    }
    return this.request<{ message: string }>(
      `/api/embeddings/umap/recompute?${params}`,
      { method: "POST" }
    );
  }

  async getEmbeddingStatus(experimentId?: number) {
    const params = experimentId ? `?experiment_id=${experimentId}` : "";
    return this.request<EmbeddingStatus>(`/api/embeddings/status${params}`);
  }

  async triggerFeatureExtraction(experimentId: number) {
    return this.request<{ message: string; pending: number }>(
      `/api/embeddings/extract?experiment_id=${experimentId}`,
      { method: "POST" }
    );
  }

  async triggerFovFeatureExtraction(experimentId?: number) {
    const params = experimentId ? `?experiment_id=${experimentId}` : "";
    return this.request<{ message: string; pending: number }>(
      `/api/embeddings/extract-fov${params}`,
      { method: "POST" }
    );
  }

  // Settings
  async getSettings() {
    return this.request<UserSettings>("/api/settings");
  }

  async updateSettings(data: UserSettingsUpdate) {
    return this.request<UserSettings>("/api/settings", {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  }

  async updateProfile(data: ProfileUpdate) {
    return this.request<User>("/api/settings/profile", {
      method: "PATCH",
      body: JSON.stringify(data),
    });
  }

  async changePassword(data: PasswordChangeRequest) {
    return this.request<{ message: string }>("/api/settings/password", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async uploadAvatar(file: File) {
    const formData = new FormData();
    formData.append("file", file);

    // For file uploads, we need to let the browser set the Content-Type
    // with the proper multipart boundary - so we make a direct fetch call
    const url = `${API_URL}/api/settings/avatar`;
    const token = this.getToken();

    const response = await fetch(url, {
      method: "POST",
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: formData,
    });

    if (!response.ok) {
      let errorDetail: string;
      try {
        const error = await response.json();
        errorDetail = error.detail || "Upload failed";
      } catch {
        errorDetail = `Upload failed: ${response.status}`;
      }
      throw new Error(errorDetail);
    }

    return response.json() as Promise<AvatarUploadResponse>;
  }

  async deleteAvatar() {
    return this.request<{ message: string }>("/api/settings/avatar", {
      method: "DELETE",
    });
  }

  // ============================================================================
  // Segmentation (SAM)
  // ============================================================================

  /**
   * Trigger SAM embedding computation for an image.
   * This is GPU-intensive and runs in background.
   */
  async computeSAMEmbedding(imageId: number) {
    return this.request<{ message: string; image_id: number }>(
      `/api/segmentation/compute-embedding/${imageId}`,
      { method: "POST" }
    );
  }

  /**
   * Check SAM embedding status for an image.
   */
  async getSAMEmbeddingStatus(imageId: number) {
    return this.request<SAMEmbeddingStatusResponse>(
      `/api/segmentation/embedding-status/${imageId}`
    );
  }

  /**
   * Run interactive segmentation from click prompts.
   * Requires pre-computed embedding (status = "ready").
   */
  async segmentInteractive(imageId: number, points: SegmentClickPoint[]) {
    return this.request<SegmentResponse>("/api/segmentation/segment", {
      method: "POST",
      body: JSON.stringify({ image_id: imageId, points }),
    });
  }

  /**
   * Save finalized segmentation mask for a crop.
   */
  async saveSegmentationMask(data: {
    crop_id: number;
    polygon: [number, number][];
    iou_score: number;
    prompt_count: number;
  }) {
    return this.request<{ success: boolean; crop_id: number; area_pixels: number }>(
      "/api/segmentation/save-mask",
      {
        method: "POST",
        body: JSON.stringify(data),
      }
    );
  }

  /**
   * Get segmentation mask for a crop.
   */
  async getSegmentationMask(cropId: number) {
    return this.request<SegmentationMaskResponse>(
      `/api/segmentation/mask/${cropId}`
    );
  }

  /**
   * Get segmentation masks for multiple crops at once.
   */
  async getSegmentationMasksBatch(cropIds: number[]) {
    return this.request<SegmentationMasksBatchResponse>(
      `/api/segmentation/masks/batch?crop_ids=${cropIds.join(",")}`
    );
  }

  /**
   * Delete segmentation mask for a crop.
   */
  async deleteSegmentationMask(cropId: number) {
    return this.request<{ success: boolean; crop_id: number }>(
      `/api/segmentation/mask/${cropId}`,
      { method: "DELETE" }
    );
  }

  // Bug Reports
  async submitBugReport(data: BugReportCreate) {
    return this.request<BugReport>("/api/bug-reports", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async getMyBugReports() {
    return this.request<BugReportListResponse>("/api/bug-reports");
  }

  /**
   * Get the full URL for a user's avatar.
   * Returns undefined if the user has no avatar or if the path is invalid.
   *
   * Valid avatar paths must start with /uploads/ to ensure they point to
   * the static files directory, not to API endpoints.
   */
  getAvatarUrl(avatarPath: string | undefined): string | undefined {
    if (!avatarPath) return undefined;
    // Only allow paths that start with /uploads/ to prevent loading from API endpoints
    if (!avatarPath.startsWith('/uploads/')) {
      console.warn(`[API] Invalid avatar path: ${avatarPath}. Avatar paths must start with /uploads/`);
      return undefined;
    }
    return `${API_URL}${avatarPath}`;
  }
}

// Types
export interface User {
  id: number;
  email: string;
  name: string;
  role: "viewer" | "researcher" | "admin";
  avatar_url?: string;
  created_at: string;
}

export interface Experiment {
  id: number;
  name: string;
  description?: string;
  status: "draft" | "active" | "completed" | "archived";
  created_at: string;
  updated_at: string;
  image_count: number;
  cell_count: number;
}

export interface MapProtein {
  id: number;
  name: string;
  full_name?: string;
  description?: string;
  color?: string;
}

export type ImageStatus = "UPLOADING" | "UPLOADED" | "PROCESSING" | "DETECTING" | "EXTRACTING_FEATURES" | "READY" | "ERROR";

export interface Image {
  id: number;
  experiment_id: number;
  original_filename: string;
  status: ImageStatus;
  width?: number;
  height?: number;
  z_slices?: number;
  file_size?: number;
  created_at: string;
  map_protein?: MapProtein;
  cell_count: number;
  detect_cells?: boolean;
  error_message?: string;
}

export interface BatchProcessResponse {
  processing_count: number;
  message: string;
}

export interface FOVImage {
  id: number;
  experiment_id: number;
  original_filename: string;
  status: ImageStatus;
  width?: number;
  height?: number;
  z_slices?: number;
  file_size?: number;
  detect_cells: boolean;
  thumbnail_url?: string;
  cell_count: number;
  map_protein?: MapProtein;
  created_at: string;
  processed_at?: string;
}

export interface CellCrop {
  id: number;
  image_id: number;
  mip_url?: string;
  map_protein_name?: string;
  bundleness_score?: number;
}

export interface CellCropGallery {
  id: number;
  image_id: number;
  parent_filename: string;
  bbox_x: number;
  bbox_y: number;
  bbox_w: number;
  bbox_h: number;
  bundleness_score?: number;
  detection_confidence?: number;
  excluded: boolean;
  created_at: string;
  map_protein_name?: string;
  map_protein_color?: string;
}

export interface PairResponse {
  crop_a: CellCrop;
  crop_b: CellCrop;
  comparison_number: number;
  total_comparisons: number;
}

export interface Comparison {
  id: number;
  crop_a_id: number;
  crop_b_id: number;
  winner_id: number;
  timestamp: string;
}

export interface RankingItem {
  rank: number;
  cell_crop_id: number;
  image_id: number;
  mip_url?: string;
  map_protein_name?: string;
  mu: number;
  sigma: number;
  ordinal_score: number;
  comparison_count: number;
  bundleness_score?: number;
}

export interface RankingResponse {
  items: RankingItem[];
  total: number;
  page: number;
  per_page: number;
}

export interface ProgressResponse {
  total_comparisons: number;
  convergence_percent: number;
  estimated_remaining: number;
  average_sigma: number;
  target_sigma: number;
  phase: "exploration" | "exploitation";
}

// Metric types
export interface Metric {
  id: number;
  name: string;
  description?: string;
  image_count: number;
  comparison_count: number;
  created_at: string;
  updated_at: string;
}

export interface MetricListResponse {
  items: Metric[];
  total: number;
}

export interface MetricImage {
  id: number;
  metric_id: number;
  cell_crop_id?: number;
  file_path?: string;
  original_filename?: string;
  image_url?: string;
  created_at: string;
  mu?: number;
  sigma?: number;
  ordinal_score?: number;
  comparison_count: number;
}

export interface MetricImageForRanking {
  id: number;
  image_url?: string;
  cell_crop_id?: number;
  original_filename?: string;
}

export interface MetricPairResponse {
  image_a: MetricImageForRanking;
  image_b: MetricImageForRanking;
  comparison_number: number;
  total_comparisons: number;
}

export interface MetricComparison {
  id: number;
  metric_id: number;
  image_a_id: number;
  image_b_id: number;
  winner_id: number;
  created_at: string;
}

export interface MetricRankingItem {
  rank: number;
  metric_image_id: number;
  image_url?: string;
  cell_crop_id?: number;
  original_filename?: string;
  mu: number;
  sigma: number;
  ordinal_score: number;
  comparison_count: number;
}

export interface MetricRankingResponse {
  items: MetricRankingItem[];
  total: number;
  page: number;
  per_page: number;
}

export interface MetricProgressResponse {
  total_comparisons: number;
  convergence_percent: number;
  estimated_remaining: number;
  average_sigma: number;
  target_sigma: number;
  phase: "exploration" | "exploitation";
  image_count: number;
}

export interface ExperimentForImport {
  id: number;
  name: string;
  image_count: number;
  crop_count: number;
  already_imported: number;
}

// UMAP / Embeddings types
export type UmapType = "fov" | "cropped";

export interface UmapPoint {
  crop_id: number;
  image_id: number;
  x: number;
  y: number;
  protein_name: string | null;
  protein_color: string;
  thumbnail_url: string;
  bundleness_score: number | null;
}

export interface UmapDataResponse {
  points: UmapPoint[];
  total_crops: number;
  n_neighbors: number;
  min_dist: number;
  silhouette_score: number | null;
}

export interface UmapFovPoint {
  image_id: number;
  experiment_id: number;
  x: number;
  y: number;
  protein_name: string | null;
  protein_color: string;
  thumbnail_url: string;
  original_filename: string;
}

export interface UmapFovDataResponse {
  points: UmapFovPoint[];
  total_images: number;
  silhouette_score: number | null;
  is_precomputed: boolean;
  computed_at: string | null;
}

export interface EmbeddingStatus {
  total: number;
  with_embeddings: number;
  without_embeddings: number;
  percentage: number;
}

// Settings types
export type DisplayMode = "grayscale" | "inverted" | "green" | "fire";
export type Theme = "dark" | "light";
export type Language = "en" | "fr";

export interface UserSettings {
  display_mode: DisplayMode;
  theme: Theme;
  language: Language;
}

export interface UserSettingsUpdate {
  display_mode?: DisplayMode;
  theme?: Theme;
  language?: Language;
}

export interface ProfileUpdate {
  name?: string;
  email?: string;
}

export interface PasswordChangeRequest {
  current_password: string;
  new_password: string;
  confirm_password: string;
}

export interface AvatarUploadResponse {
  avatar_url: string;
  message?: string;
}

// Bug Report types
export type BugReportCategory = "bug" | "feature" | "other";
export type BugReportStatus = "open" | "in_progress" | "resolved" | "closed";

export interface BugReportCreate {
  description: string;
  category: BugReportCategory;
  browser_info?: string;
  page_url?: string;
  screen_resolution?: string;
  user_settings_json?: string;
}

export interface BugReport {
  id: number;
  user_id: number;
  user_name: string;
  user_email: string;
  description: string;
  category: BugReportCategory;
  status: BugReportStatus;
  browser_info?: string;
  page_url?: string;
  screen_resolution?: string;
  user_settings_json?: string;
  created_at: string;
}

export interface BugReportListResponse {
  reports: BugReport[];
  total: number;
}

// Crop Editor types
export interface CropBboxUpdateResponse {
  id: number;
  bbox_x: number;
  bbox_y: number;
  bbox_w: number;
  bbox_h: number;
  needs_regeneration: boolean;
}

export interface ManualCropCreateResponse {
  id: number;
  image_id: number;
  bbox_x: number;
  bbox_y: number;
  bbox_w: number;
  bbox_h: number;
  detection_confidence: number | null;
  needs_processing: boolean;
}

export interface CropRegenerateResponse {
  id: number;
  bbox_x: number;
  bbox_y: number;
  bbox_w: number;
  bbox_h: number;
  mip_path: string | null;
  sum_crop_path: string | null;
  mean_intensity: number | null;
  embedding_model: string | null;
  has_embedding: boolean;
  processing_status: "completed" | "processing" | "failed";
}

export interface CropBatchUpdateItem {
  id?: number;
  action: "create" | "update" | "delete";
  bbox_x?: number;
  bbox_y?: number;
  bbox_w?: number;
  bbox_h?: number;
  map_protein_id?: number;
}

export interface CropBatchUpdateResponse {
  created: number[];
  updated: number[];
  deleted: number[];
  failed: { action: string; id?: number; error: string }[];
  regeneration_queued: boolean;
}

// ============================================================================
// Segmentation types
// ============================================================================

// Re-export from canonical location to avoid DRY violation
export type { SAMEmbeddingStatus } from "@/lib/editor/types";
import type { SAMEmbeddingStatus } from "@/lib/editor/types";

export interface SAMEmbeddingStatusResponse {
  image_id: number;
  status: SAMEmbeddingStatus;
  has_embedding: boolean;
  embedding_shape?: string;
  model_variant?: string;
}

export interface SegmentClickPoint {
  x: number;
  y: number;
  label: 0 | 1; // 0 = background, 1 = foreground
}

export interface SegmentResponse {
  success: boolean;
  polygon?: [number, number][];
  iou_score?: number;
  area_pixels?: number;
  error?: string;
}

export interface SegmentationMaskResponse {
  has_mask: boolean;
  polygon?: [number, number][];
  iou_score?: number;
  area_pixels?: number;
  creation_method?: string;
  prompt_count?: number;
}

export interface SegmentationMasksBatchResponse {
  masks: Record<number, {
    polygon: [number, number][];
    iou_score: number;
    area_pixels: number;
    creation_method: string;
  }>;
}

export const api = new ApiClient();
