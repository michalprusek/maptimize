"""
GPU Model Lifecycle Manager.

Centralizes model loading/unloading to share limited GPU memory
between multiple ML models. Models are loaded on-demand and
automatically unloaded after a configurable idle timeout.

Configuration is read from config.Settings (SSOT for all app settings).
"""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class ModelState(Enum):
    UNLOADED = "unloaded"
    LOADING = "loading"
    LOADED = "loaded"


@dataclass
class ManagedModel:
    """Tracks registration info and runtime state for a GPU-managed model."""
    name: str
    get_fn: Callable[[], Any]
    reset_fn: Callable[[], None]
    estimated_vram_mb: int
    state: ModelState = ModelState.UNLOADED
    last_used: float = 0.0
    load_count: int = 0
    _load_event: Optional[threading.Event] = None


class GPUModelManager:
    """
    Centralized GPU model lifecycle manager.

    Thread-safe singleton. External model access should go through acquire()
    to ensure usage tracking and memory management.
    A background asyncio task periodically evicts idle models.
    """

    _instance: Optional["GPUModelManager"] = None
    _init_lock = threading.Lock()

    def __init__(self):
        from config import get_settings
        settings = get_settings()

        self._models: Dict[str, ManagedModel] = {}
        self._lock = threading.RLock()
        self._cleanup_task: Optional[asyncio.Task] = None

        self.idle_timeout = settings.gpu_model_idle_timeout_seconds
        self.cleanup_interval = settings.gpu_model_cleanup_interval_seconds
        self.memory_limit_mb = int(settings.ml_memory_limit_gb * 1024)

        logger.info(
            "GPU Model Manager initialized: "
            "idle_timeout=%ds, cleanup_interval=%ds, memory_limit=%dMB",
            self.idle_timeout, self.cleanup_interval, self.memory_limit_mb,
        )

    @classmethod
    def get_instance(cls) -> "GPUModelManager":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        with cls._init_lock:
            cls._instance = None

    @property
    def model_count(self) -> int:
        """Number of registered models."""
        return len(self._models)

    def register(
        self,
        name: str,
        get_fn: Callable[[], Any],
        reset_fn: Callable[[], None],
        estimated_vram_mb: int,
    ) -> None:
        """Register a model for lifecycle management."""
        with self._lock:
            self._models[name] = ManagedModel(
                name=name,
                get_fn=get_fn,
                reset_fn=reset_fn,
                estimated_vram_mb=estimated_vram_mb,
            )
            logger.info("Registered model '%s' (~%dMB VRAM)", name, estimated_vram_mb)

    def acquire(self, name: str) -> Any:
        """
        Get a model instance, loading it if necessary.

        Updates last_used timestamp. If GPU memory budget would be exceeded,
        evicts least-recently-used models first. Thread-safe: concurrent
        callers block on an Event if another thread is already loading.
        """
        with self._lock:
            model = self._models.get(name)
            if model is None:
                raise ValueError(f"Unknown model: {name}")

            if model.state == ModelState.LOADED:
                model.last_used = time.monotonic()
                return model.get_fn()

            if model.state == ModelState.LOADING:
                # Another thread is loading — grab the event before releasing lock
                event = model._load_event
            else:
                # We will load — set up event for other waiters
                self._evict_if_needed(model.estimated_vram_mb, exclude=name)
                model.state = ModelState.LOADING
                model._load_event = threading.Event()
                event = None

        if event is not None:
            # Wait for the loading thread to finish (up to 5 min for large models)
            event.wait(timeout=300)
            with self._lock:
                if model.state == ModelState.LOADED:
                    model.last_used = time.monotonic()
                    return model.get_fn()
                raise RuntimeError(f"Model '{model.name}' failed to load in another thread")

        # We are the loading thread — load outside the lock
        try:
            logger.info(
                "Loading model '%s' (~%dMB VRAM)...",
                name, model.estimated_vram_mb,
            )
            instance = model.get_fn()

            with self._lock:
                model.state = ModelState.LOADED
                model.last_used = time.monotonic()
                model.load_count += 1
                load_event = model._load_event
                model._load_event = None
                logger.info(
                    "Model '%s' loaded successfully (load #%d)",
                    name, model.load_count,
                )
            # Wake all waiting threads
            if load_event:
                load_event.set()
            return instance

        except Exception:
            with self._lock:
                model.state = ModelState.UNLOADED
                load_event = model._load_event
                model._load_event = None
            if load_event:
                load_event.set()
            raise

    def release(self, name: str) -> bool:
        """Unload a specific model. Returns True if it was loaded."""
        with self._lock:
            model = self._models.get(name)
            if model is None or model.state != ModelState.LOADED:
                return False
            self._unload_model(model)
            return True

    def release_all(self) -> int:
        """Unload all loaded models. Returns count of models unloaded."""
        with self._lock:
            count = 0
            for model in self._models.values():
                if model.state == ModelState.LOADED:
                    self._unload_model(model)
                    count += 1
            if count:
                logger.info(f"Released all models ({count} unloaded)")
            return count

    def _unload_model(self, model: ManagedModel) -> None:
        """Unload a model by calling its reset function.

        Must be called with self._lock held.
        Note: reset_fn() may perform slow GPU operations while the lock is held.
        """
        logger.info(f"Unloading model '{model.name}'...")
        try:
            model.reset_fn()
        except Exception:
            logger.exception(
                "Failed to reset model '%s' -- marking as UNLOADED but GPU memory "
                "may still be consumed",
                model.name,
            )
        model.state = ModelState.UNLOADED

    def _evict_if_needed(self, required_mb: int, exclude: str = "") -> None:
        """Evict LRU models if loading would exceed memory budget.

        Note: Caller must hold self._lock.
        """
        current_usage = self._get_estimated_usage_mb()

        if current_usage + required_mb <= self.memory_limit_mb:
            return

        # Sort loaded models by last_used (oldest first), excluding the one being loaded
        loaded = sorted(
            [
                m for m in self._models.values()
                if m.state == ModelState.LOADED and m.name != exclude
            ],
            key=lambda m: m.last_used,
        )

        for model in loaded:
            if current_usage + required_mb <= self.memory_limit_mb:
                break
            idle_seconds = time.monotonic() - model.last_used
            logger.info(
                f"Evicting LRU model '{model.name}' "
                f"(idle {idle_seconds:.0f}s, ~{model.estimated_vram_mb}MB)"
            )
            self._unload_model(model)
            current_usage -= model.estimated_vram_mb

    def _get_estimated_usage_mb(self) -> int:
        """Sum of estimated VRAM for all loaded models."""
        return sum(
            m.estimated_vram_mb
            for m in self._models.values()
            if m.state == ModelState.LOADED
        )

    async def start_cleanup_task(self) -> None:
        """Start the periodic idle model cleanup task."""
        self._cleanup_task = asyncio.create_task(
            self._periodic_cleanup(), name="gpu-cleanup"
        )
        self._cleanup_task.add_done_callback(self._on_cleanup_done)
        logger.info("GPU cleanup task started")

    @staticmethod
    def _on_cleanup_done(task: asyncio.Task) -> None:
        """Log if the cleanup task exits unexpectedly."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "GPU cleanup task died unexpectedly: %s: %s",
                type(exc).__name__, exc, exc_info=exc,
            )

    async def stop_cleanup_task(self) -> None:
        """Stop the periodic cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("GPU cleanup task stopped")

    async def _periodic_cleanup(self) -> None:
        """Periodically unload idle models."""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                now = time.monotonic()
                unloaded = []

                with self._lock:
                    for model in list(self._models.values()):
                        if (
                            model.state == ModelState.LOADED
                            and (now - model.last_used) > self.idle_timeout
                        ):
                            idle_secs = now - model.last_used
                            logger.info(
                                f"Idle timeout: unloading '{model.name}' "
                                f"(idle {idle_secs:.0f}s > {self.idle_timeout}s)"
                            )
                            self._unload_model(model)
                            unloaded.append(model.name)

                if unloaded:
                    logger.info(f"Cleanup cycle: unloaded {unloaded}")

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("GPU cleanup cycle failed -- will retry next interval")

    def get_status(self) -> dict:
        """Get GPU memory status and per-model state for admin API."""
        gpu_info: dict = {"available": False}

        try:
            import torch
            if torch.cuda.is_available():
                props = torch.cuda.get_device_properties(0)
                gpu_info = {
                    "available": True,
                    "device_name": props.name,
                    "total_memory_mb": props.total_mem // (1024 * 1024),
                    "allocated_mb": int(torch.cuda.memory_allocated(0) / (1024 * 1024)),
                    "reserved_mb": int(torch.cuda.memory_reserved(0) / (1024 * 1024)),
                    "free_mb": int(
                        (props.total_mem - torch.cuda.memory_allocated(0))
                        / (1024 * 1024)
                    ),
                }
        except ImportError:
            gpu_info = {"available": False, "error": "torch not installed"}
            logger.error("torch import failed in GPU status check")
        except RuntimeError as e:
            gpu_info = {"available": False, "error": f"CUDA error: {e}"}
            logger.error("CUDA error in GPU status check: %s", e, exc_info=True)
        except Exception as e:
            gpu_info = {"available": False, "error": f"{type(e).__name__}: {e}"}
            logger.exception("Unexpected error querying GPU info")

        now = time.monotonic()
        models_info = []
        with self._lock:
            for m in self._models.values():
                models_info.append({
                    "name": m.name,
                    "state": m.state.value,
                    "estimated_vram_mb": m.estimated_vram_mb,
                    "last_used_seconds_ago": (
                        round(now - m.last_used, 1) if m.last_used > 0 else None
                    ),
                    "load_count": m.load_count,
                })
            total_usage = self._get_estimated_usage_mb()

        return {
            "gpu": gpu_info,
            "config": {
                "memory_limit_mb": self.memory_limit_mb,
                "idle_timeout_seconds": self.idle_timeout,
                "cleanup_interval_seconds": self.cleanup_interval,
            },
            "models": models_info,
            "total_estimated_usage_mb": total_usage,
        }


def get_gpu_manager() -> GPUModelManager:
    """Get the global GPU model manager instance."""
    return GPUModelManager.get_instance()
