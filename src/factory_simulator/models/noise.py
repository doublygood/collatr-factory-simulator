"""Noise pipeline: distributions, speed-dependent sigma, Cholesky correlation.

Three noise distributions are supported:
- **Gaussian**: ``sigma * N(0, 1)``
- **Student-t**: ``sigma * T(df)`` (intentionally higher RMS -- PRD 4.2.11)
- **AR(1)**: ``phi * prev + sigma * sqrt(1 - phi^2) * N(0, 1)``

Speed-dependent sigma: ``effective_sigma = sigma_base + sigma_scale * |parent|``

Cholesky correlation pipeline (PRD 4.3.1):
1. Generate N independent N(0,1) samples.
2. Apply Cholesky factor L: ``correlated = L @ independent``.
3. Scale by effective sigma per signal.

PRD Reference: Section 4.2.11 (Noise Distributions), Section 4.3.1 (Cholesky)
CLAUDE.md Rule 13: numpy.random.Generator with SeedSequence -- never random module.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

# ---------------------------------------------------------------------------
# NoiseGenerator -- per-signal noise source
# ---------------------------------------------------------------------------


class NoiseGenerator:
    """Generates noise samples for a single signal.

    Parameters
    ----------
    sigma:
        Base noise standard deviation.
    distribution:
        One of ``"gaussian"``, ``"student_t"``, ``"ar1"``.
    rng:
        numpy random Generator (from SeedSequence).
    df:
        Degrees of freedom for Student-t distribution.  Required when
        ``distribution="student_t"``.  Must be >= 3.
    phi:
        Autocorrelation coefficient for AR(1).  Required when
        ``distribution="ar1"``.  Must be in (-1, 1).
    sigma_base:
        Minimum noise floor for speed-dependent sigma.  When set,
        ``effective_sigma = sigma_base + sigma_scale * |parent_value|``.
        If *None*, speed-dependent sigma is disabled and ``sigma`` is used.
    sigma_scale:
        Proportional noise component for speed-dependent sigma.
    """

    def __init__(
        self,
        sigma: float,
        distribution: str,
        rng: np.random.Generator,
        *,
        df: float | None = None,
        phi: float | None = None,
        sigma_base: float | None = None,
        sigma_scale: float = 0.0,
    ) -> None:
        if sigma < 0:
            raise ValueError("sigma must be non-negative")

        allowed = {"gaussian", "student_t", "ar1"}
        if distribution not in allowed:
            raise ValueError(f"distribution must be one of {sorted(allowed)}")

        if distribution == "student_t":
            if df is None:
                raise ValueError("df is required for student_t distribution")
            if df < 3:
                raise ValueError("df must be >= 3 for student_t distribution")

        if distribution == "ar1":
            if phi is None:
                raise ValueError("phi is required for ar1 distribution")
            if not -1.0 < phi < 1.0:
                raise ValueError("phi must be in (-1, 1)")

        self._sigma = sigma
        self._distribution = distribution
        self._rng = rng
        self._df = df if df is not None else 5.0
        self._phi = phi if phi is not None else 0.0

        # Speed-dependent sigma
        self._sigma_base = sigma_base
        self._sigma_scale = sigma_scale

        # AR(1) state
        self._ar1_prev: float = 0.0

    @property
    def sigma(self) -> float:
        """Base sigma."""
        return self._sigma

    @property
    def distribution(self) -> str:
        """Distribution type."""
        return self._distribution

    def effective_sigma(self, parent_value: float | None = None) -> float:
        """Compute effective sigma, optionally speed-dependent.

        Parameters
        ----------
        parent_value:
            Current value of the parent signal for speed-dependent sigma.
            If *None* or speed-dependent sigma is not configured, returns
            the base sigma.
        """
        if self._sigma_base is not None and parent_value is not None:
            return self._sigma_base + self._sigma_scale * abs(parent_value)
        return self._sigma

    def sample(self, parent_value: float | None = None) -> float:
        """Draw one noise sample.

        Parameters
        ----------
        parent_value:
            Optional parent signal value for speed-dependent sigma.

        Returns
        -------
        float
            A single noise sample scaled by effective sigma.
        """
        sigma = self.effective_sigma(parent_value)

        if sigma == 0.0:
            return 0.0

        if self._distribution == "gaussian":
            return float(sigma * self._rng.standard_normal())

        if self._distribution == "student_t":
            return float(sigma * self._rng.standard_t(self._df))

        # AR(1): noise_t = phi * noise_(t-1) + sigma * sqrt(1 - phi^2) * N(0,1)
        innovation_scale = sigma * np.sqrt(1.0 - self._phi**2)
        self._ar1_prev = (
            self._phi * self._ar1_prev
            + innovation_scale * self._rng.standard_normal()
        )
        return float(self._ar1_prev)

    def reset(self) -> None:
        """Reset internal state (AR(1) memory)."""
        self._ar1_prev = 0.0

    @classmethod
    def from_config(
        cls,
        sigma: float,
        noise_type: str,
        rng: np.random.Generator,
        *,
        noise_df: float | None = None,
        noise_phi: float | None = None,
        sigma_base: float | None = None,
        sigma_scale: float = 0.0,
    ) -> NoiseGenerator:
        """Create a NoiseGenerator from signal config fields.

        Maps the config field names (``noise_type``, ``noise_df``,
        ``noise_phi``) to the constructor parameters.
        """
        return cls(
            sigma=sigma,
            distribution=noise_type,
            rng=rng,
            df=noise_df,
            phi=noise_phi,
            sigma_base=sigma_base,
            sigma_scale=sigma_scale,
        )


# ---------------------------------------------------------------------------
# CholeskyCorrelator -- peer correlation via Cholesky decomposition
# ---------------------------------------------------------------------------


class CholeskyCorrelator:
    """Applies peer correlation to independent noise samples.

    Given a symmetric, positive-definite correlation matrix R, computes
    the lower-triangular Cholesky factor L at construction.  Each call
    to :meth:`correlate` takes N independent N(0,1) samples and produces
    N correlated samples with unit variance and the specified correlations.

    The signal generation pipeline order (PRD 4.3.1):
    1. Generate N independent N(0,1) samples.
    2. Apply L: ``correlated = L @ independent``.
    3. Scale by per-signal effective sigma (done externally).

    Parameters
    ----------
    correlation_matrix:
        N x N symmetric positive-definite matrix with unit diagonal.
    """

    def __init__(self, correlation_matrix: NDArray[np.float64]) -> None:
        R = np.asarray(correlation_matrix, dtype=np.float64)

        if R.ndim != 2 or R.shape[0] != R.shape[1]:
            raise ValueError("correlation_matrix must be a square 2D array")

        # Validate symmetric
        if not np.allclose(R, R.T):
            raise ValueError("correlation_matrix must be symmetric")

        # Validate unit diagonal
        if not np.allclose(np.diag(R), 1.0):
            raise ValueError("correlation_matrix diagonal must be 1.0")

        # Compute lower-triangular Cholesky factor
        self._L: NDArray[np.float64] = np.linalg.cholesky(R).astype(np.float64)
        self._n: int = int(R.shape[0])

    @property
    def n(self) -> int:
        """Number of correlated signals."""
        return self._n

    @property
    def L(self) -> NDArray[np.float64]:
        """Lower-triangular Cholesky factor (read-only copy)."""
        return self._L.copy()

    def correlate(
        self, independent: NDArray[np.float64]
    ) -> NDArray[np.float64]:
        """Apply correlation to independent N(0,1) samples.

        Parameters
        ----------
        independent:
            Array of shape ``(N,)`` with N independent N(0,1) samples.

        Returns
        -------
        NDArray[np.float64]
            Correlated samples of shape ``(N,)`` with unit variance.
        """
        ind = np.asarray(independent, dtype=np.float64)
        if ind.shape != (self._n,):
            raise ValueError(
                f"Expected {self._n} independent samples, got shape {ind.shape}"
            )
        return self._L @ ind

    def generate_correlated(
        self,
        rng: np.random.Generator,
        sigmas: NDArray[np.float64] | None = None,
    ) -> NDArray[np.float64]:
        """Convenience: generate correlated noise in one call.

        Follows the full pipeline from PRD 4.3.1:
        1. Generate N independent N(0,1).
        2. Apply Cholesky factor L.
        3. Scale by per-signal sigma (if provided).

        Parameters
        ----------
        rng:
            numpy random Generator.
        sigmas:
            Optional array of shape ``(N,)`` with per-signal effective sigmas.
            If *None*, correlated samples have unit variance.

        Returns
        -------
        NDArray[np.float64]
            Array of shape ``(N,)`` with correlated, sigma-scaled noise.
        """
        independent = rng.standard_normal(self._n)
        correlated = self._L @ independent
        if sigmas is not None:
            s = np.asarray(sigmas, dtype=np.float64)
            if s.shape != (self._n,):
                raise ValueError(
                    f"sigmas must have shape ({self._n},), got {s.shape}"
                )
            correlated = correlated * s
        return correlated
