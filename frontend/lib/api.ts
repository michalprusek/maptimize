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
  async uploadImage(
    experimentId: number,
    file: File,
    mapProteinId?: number,
    detectCells: boolean = true
  ) {
    const formData = new FormData();
    formData.append("experiment_id", experimentId.toString());
    formData.append("file", file);
    if (mapProteinId) {
      formData.append("map_protein_id", mapProteinId.toString());
    }
    formData.append("detect_cells", detectCells.toString());

    return this.request<Image>("/api/images/upload", {
      method: "POST",
      body: formData,
    });
  }

  async getImages(experimentId: number) {
    return this.request<Image[]>(`/api/images?experiment_id=${experimentId}`);
  }

  async getImage(id: number) {
    return this.request<Image>(`/api/images/${id}`);
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

  // Embeddings / UMAP
  async getUmapData(experimentId?: number, nNeighbors = 15, minDist = 0.1) {
    const params = new URLSearchParams({
      n_neighbors: nNeighbors.toString(),
      min_dist: minDist.toString(),
    });
    if (experimentId) {
      params.append("experiment_id", experimentId.toString());
    }
    return this.request<UmapDataResponse>(`/api/embeddings/umap?${params}`);
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

export interface Image {
  id: number;
  experiment_id: number;
  original_filename: string;
  status: "uploading" | "processing" | "detecting" | "extracting_features" | "ready" | "error";
  width?: number;
  height?: number;
  z_slices?: number;
  file_size?: number;
  created_at: string;
  map_protein?: MapProtein;
  cell_count: number;
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

export interface EmbeddingStatus {
  total: number;
  with_embeddings: number;
  without_embeddings: number;
  percentage: number;
}

export const api = new ApiClient();
