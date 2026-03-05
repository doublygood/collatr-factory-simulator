"""Tests for the noise pipeline: NoiseGenerator and CholeskyCorrelator.

Property-based tests with Hypothesis validate statistical properties:
- Gaussian noise: mean ~0, stddev ~sigma
- Student-t: heavier tails than Gaussian (kurtosis > 3)
- AR(1): lag-1 autocorrelation matches phi
- Cholesky: output correlations match specification
- Speed-dependent sigma scales correctly
- Determinism with same seed

PRD Reference: Section 4.2.11, 4.3.1
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from factory_simulator.models.noise import CholeskyCorrelator, NoiseGenerator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

N_SAMPLES = 10_000
SEED = 42


def make_rng(seed: int = SEED) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence(seed))


def excess_kurtosis(samples: np.ndarray) -> float:
    """Fisher excess kurtosis (0 for Gaussian) computed with numpy."""
    m = np.mean(samples)
    s = np.std(samples, ddof=0)
    return float(np.mean(((samples - m) / s) ** 4) - 3.0)


# ---------------------------------------------------------------------------
# NoiseGenerator -- construction
# ---------------------------------------------------------------------------


class TestNoiseGeneratorConstruction:
    """Validation of constructor arguments."""

    def test_gaussian_default(self) -> None:
        ng = NoiseGenerator(sigma=1.0, distribution="gaussian", rng=make_rng())
        assert ng.sigma == 1.0
        assert ng.distribution == "gaussian"

    def test_student_t_requires_df(self) -> None:
        with pytest.raises(ValueError, match="df is required"):
            NoiseGenerator(sigma=1.0, distribution="student_t", rng=make_rng())

    def test_student_t_df_minimum(self) -> None:
        with pytest.raises(ValueError, match="df must be >= 3"):
            NoiseGenerator(
                sigma=1.0, distribution="student_t", rng=make_rng(), df=2.0
            )

    def test_student_t_valid(self) -> None:
        ng = NoiseGenerator(
            sigma=1.0, distribution="student_t", rng=make_rng(), df=5.0
        )
        assert ng.distribution == "student_t"

    def test_ar1_requires_phi(self) -> None:
        with pytest.raises(ValueError, match="phi is required"):
            NoiseGenerator(sigma=1.0, distribution="ar1", rng=make_rng())

    def test_ar1_phi_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="phi must be in"):
            NoiseGenerator(
                sigma=1.0, distribution="ar1", rng=make_rng(), phi=1.0
            )
        with pytest.raises(ValueError, match="phi must be in"):
            NoiseGenerator(
                sigma=1.0, distribution="ar1", rng=make_rng(), phi=-1.0
            )

    def test_ar1_valid(self) -> None:
        ng = NoiseGenerator(
            sigma=1.0, distribution="ar1", rng=make_rng(), phi=0.7
        )
        assert ng.distribution == "ar1"

    def test_negative_sigma_rejected(self) -> None:
        with pytest.raises(ValueError, match="sigma must be non-negative"):
            NoiseGenerator(sigma=-1.0, distribution="gaussian", rng=make_rng())

    def test_invalid_distribution_rejected(self) -> None:
        with pytest.raises(ValueError, match="distribution must be one of"):
            NoiseGenerator(sigma=1.0, distribution="pink", rng=make_rng())

    def test_zero_sigma_returns_zero(self) -> None:
        ng = NoiseGenerator(sigma=0.0, distribution="gaussian", rng=make_rng())
        for _ in range(100):
            assert ng.sample() == 0.0


# ---------------------------------------------------------------------------
# Gaussian distribution properties
# ---------------------------------------------------------------------------


class TestGaussianNoise:
    """Statistical properties of Gaussian noise."""

    def test_mean_near_zero(self) -> None:
        ng = NoiseGenerator(sigma=2.0, distribution="gaussian", rng=make_rng())
        samples = np.array([ng.sample() for _ in range(N_SAMPLES)])
        assert abs(np.mean(samples)) < 0.1  # 5-sigma for N=10000

    def test_stddev_near_sigma(self) -> None:
        sigma = 3.5
        ng = NoiseGenerator(sigma=sigma, distribution="gaussian", rng=make_rng())
        samples = np.array([ng.sample() for _ in range(N_SAMPLES)])
        assert abs(np.std(samples) - sigma) < 0.15

    @given(sigma=st.floats(min_value=0.01, max_value=100.0))
    @settings(max_examples=10, deadline=None)
    def test_stddev_scales_with_sigma(self, sigma: float) -> None:
        ng = NoiseGenerator(sigma=sigma, distribution="gaussian", rng=make_rng())
        samples = np.array([ng.sample() for _ in range(5000)])
        # Relative error within 10%
        relative_error = abs(np.std(samples) - sigma) / sigma
        assert relative_error < 0.10

    def test_kurtosis_near_three(self) -> None:
        """Gaussian excess kurtosis should be near 0 (kurtosis ~3)."""
        ng = NoiseGenerator(sigma=1.0, distribution="gaussian", rng=make_rng())
        samples = np.array([ng.sample() for _ in range(N_SAMPLES)])
        kurt = excess_kurtosis(samples)
        assert abs(kurt) < 0.3  # excess kurtosis near 0


# ---------------------------------------------------------------------------
# Student-t distribution properties
# ---------------------------------------------------------------------------


class TestStudentTNoise:
    """Statistical properties of Student-t noise."""

    def test_mean_near_zero(self) -> None:
        ng = NoiseGenerator(
            sigma=2.0, distribution="student_t", rng=make_rng(), df=5.0
        )
        samples = np.array([ng.sample() for _ in range(N_SAMPLES)])
        assert abs(np.mean(samples)) < 0.15

    def test_heavier_tails_than_gaussian(self) -> None:
        """Student-t with df=5 should have kurtosis > Gaussian (excess > 0)."""
        ng = NoiseGenerator(
            sigma=1.0, distribution="student_t", rng=make_rng(), df=5.0
        )
        samples = np.array([ng.sample() for _ in range(N_SAMPLES)])
        kurt = excess_kurtosis(samples)
        # Student-t with df=5 has theoretical excess kurtosis = 6
        assert kurt > 1.0  # well above Gaussian's 0

    def test_df3_extreme_tails(self) -> None:
        """df=3 should produce even heavier tails."""
        ng = NoiseGenerator(
            sigma=1.0, distribution="student_t", rng=make_rng(), df=3.0
        )
        samples = np.array([ng.sample() for _ in range(N_SAMPLES)])
        kurt = excess_kurtosis(samples)
        assert kurt > 2.0

    def test_higher_rms_than_gaussian(self) -> None:
        """PRD 4.2.11: Student-t at df=5 has 29% higher RMS (intentional)."""
        sigma = 1.0
        ng = NoiseGenerator(
            sigma=sigma, distribution="student_t", rng=make_rng(), df=5.0
        )
        samples = np.array([ng.sample() for _ in range(N_SAMPLES)])
        rms = float(np.std(samples))
        # Theoretical: sigma * sqrt(df / (df - 2)) = 1.0 * sqrt(5/3) ~ 1.29
        expected_rms = sigma * np.sqrt(5.0 / 3.0)
        assert abs(rms - expected_rms) / expected_rms < 0.10


# ---------------------------------------------------------------------------
# AR(1) distribution properties
# ---------------------------------------------------------------------------


class TestAR1Noise:
    """Statistical properties of AR(1) autocorrelated noise."""

    def test_mean_near_zero(self) -> None:
        ng = NoiseGenerator(
            sigma=1.0, distribution="ar1", rng=make_rng(), phi=0.7
        )
        samples = np.array([ng.sample() for _ in range(N_SAMPLES)])
        assert abs(np.mean(samples)) < 0.15

    def test_marginal_variance_matches_sigma(self) -> None:
        """AR(1) sqrt(1-phi^2) scaling preserves marginal variance at sigma^2."""
        sigma = 2.0
        phi = 0.7
        ng = NoiseGenerator(
            sigma=sigma, distribution="ar1", rng=make_rng(), phi=phi
        )
        # Burn in
        for _ in range(500):
            ng.sample()
        samples = np.array([ng.sample() for _ in range(N_SAMPLES)])
        assert abs(np.std(samples) - sigma) / sigma < 0.10

    def test_lag1_autocorrelation_matches_phi(self) -> None:
        """Lag-1 autocorrelation should approximate phi."""
        phi = 0.7
        ng = NoiseGenerator(
            sigma=1.0, distribution="ar1", rng=make_rng(), phi=phi
        )
        # Burn in
        for _ in range(500):
            ng.sample()
        samples = np.array([ng.sample() for _ in range(N_SAMPLES)])
        # Compute lag-1 autocorrelation
        lag1_corr = float(np.corrcoef(samples[:-1], samples[1:])[0, 1])
        assert abs(lag1_corr - phi) < 0.05

    def test_high_phi_strong_correlation(self) -> None:
        phi = 0.95
        ng = NoiseGenerator(
            sigma=1.0, distribution="ar1", rng=make_rng(), phi=phi
        )
        for _ in range(500):
            ng.sample()
        samples = np.array([ng.sample() for _ in range(N_SAMPLES)])
        lag1_corr = float(np.corrcoef(samples[:-1], samples[1:])[0, 1])
        assert abs(lag1_corr - phi) < 0.05

    def test_low_phi_weak_correlation(self) -> None:
        phi = 0.1
        ng = NoiseGenerator(
            sigma=1.0, distribution="ar1", rng=make_rng(), phi=phi
        )
        for _ in range(500):
            ng.sample()
        samples = np.array([ng.sample() for _ in range(N_SAMPLES)])
        lag1_corr = float(np.corrcoef(samples[:-1], samples[1:])[0, 1])
        assert abs(lag1_corr - phi) < 0.05

    def test_reset_clears_state(self) -> None:
        ng = NoiseGenerator(
            sigma=1.0, distribution="ar1", rng=make_rng(), phi=0.7
        )
        for _ in range(100):
            ng.sample()
        ng.reset()
        # After reset, first sample should not depend on previous state
        # (starts from 0)
        ng2 = NoiseGenerator(
            sigma=1.0, distribution="ar1", rng=make_rng(), phi=0.7
        )
        for _ in range(100):
            ng2.sample()
        ng2.reset()
        # Both should produce the same sequence after reset with same rng state
        # (they won't because rngs have diverged, but ar1_prev is 0 in both)
        assert ng._ar1_prev == 0.0
        assert ng2._ar1_prev == 0.0


# ---------------------------------------------------------------------------
# Speed-dependent sigma
# ---------------------------------------------------------------------------


class TestSpeedDependentSigma:
    """Speed-dependent sigma: effective_sigma = sigma_base + sigma_scale * |parent|."""

    def test_constant_sigma_when_not_configured(self) -> None:
        ng = NoiseGenerator(sigma=2.0, distribution="gaussian", rng=make_rng())
        assert ng.effective_sigma() == 2.0
        assert ng.effective_sigma(parent_value=100.0) == 2.0

    def test_speed_dependent_formula(self) -> None:
        ng = NoiseGenerator(
            sigma=0.0,  # not used when sigma_base is set
            distribution="gaussian",
            rng=make_rng(),
            sigma_base=0.2,
            sigma_scale=0.015,
        )
        # At speed 0: sigma = 0.2
        assert ng.effective_sigma(parent_value=0.0) == pytest.approx(0.2)
        # At speed 100: sigma = 0.2 + 0.015 * 100 = 1.7
        assert ng.effective_sigma(parent_value=100.0) == pytest.approx(1.7)
        # At speed -50 (abs): sigma = 0.2 + 0.015 * 50 = 0.95
        assert ng.effective_sigma(parent_value=-50.0) == pytest.approx(0.95)

    def test_speed_dependent_sigma_affects_samples(self) -> None:
        ng = NoiseGenerator(
            sigma=0.0,
            distribution="gaussian",
            rng=make_rng(),
            sigma_base=0.1,
            sigma_scale=0.01,
        )
        low_speed = np.array(
            [ng.sample(parent_value=10.0) for _ in range(N_SAMPLES)]
        )
        # Reset rng for fair comparison
        ng2 = NoiseGenerator(
            sigma=0.0,
            distribution="gaussian",
            rng=make_rng(),
            sigma_base=0.1,
            sigma_scale=0.01,
        )
        high_speed = np.array(
            [ng2.sample(parent_value=200.0) for _ in range(N_SAMPLES)]
        )
        # High-speed samples should have higher variance
        assert np.std(high_speed) > np.std(low_speed) * 1.5

    def test_no_parent_value_uses_base_sigma(self) -> None:
        ng = NoiseGenerator(
            sigma=5.0,
            distribution="gaussian",
            rng=make_rng(),
            sigma_base=0.2,
            sigma_scale=0.015,
        )
        # When parent_value is None, falls back to base sigma (self._sigma)
        assert ng.effective_sigma(parent_value=None) == 5.0
        assert ng.effective_sigma() == 5.0

    @given(
        sigma_base=st.floats(min_value=0.0, max_value=10.0),
        sigma_scale=st.floats(min_value=0.0, max_value=1.0),
        parent=st.floats(min_value=0.0, max_value=500.0),
    )
    @settings(max_examples=20, deadline=None)
    def test_effective_sigma_always_non_negative(
        self, sigma_base: float, sigma_scale: float, parent: float
    ) -> None:
        ng = NoiseGenerator(
            sigma=0.0,
            distribution="gaussian",
            rng=make_rng(),
            sigma_base=sigma_base,
            sigma_scale=sigma_scale,
        )
        assert ng.effective_sigma(parent_value=parent) >= 0.0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same seed -> same output. PRD Rule 13."""

    def test_gaussian_deterministic(self) -> None:
        s1 = [
            NoiseGenerator(sigma=1.0, distribution="gaussian", rng=make_rng()).sample()
            for _ in range(100)
        ]
        s2 = [
            NoiseGenerator(sigma=1.0, distribution="gaussian", rng=make_rng()).sample()
            for _ in range(100)
        ]
        # Each call creates a new generator with same seed, so first sample matches
        assert s1 == s2

    def test_sequence_deterministic(self) -> None:
        """A full sequence from one generator is deterministic."""
        ng1 = NoiseGenerator(sigma=1.0, distribution="gaussian", rng=make_rng())
        seq1 = [ng1.sample() for _ in range(200)]

        ng2 = NoiseGenerator(sigma=1.0, distribution="gaussian", rng=make_rng())
        seq2 = [ng2.sample() for _ in range(200)]

        assert seq1 == seq2

    def test_student_t_deterministic(self) -> None:
        ng1 = NoiseGenerator(
            sigma=1.0, distribution="student_t", rng=make_rng(), df=5.0
        )
        ng2 = NoiseGenerator(
            sigma=1.0, distribution="student_t", rng=make_rng(), df=5.0
        )
        seq1 = [ng1.sample() for _ in range(200)]
        seq2 = [ng2.sample() for _ in range(200)]
        assert seq1 == seq2

    def test_ar1_deterministic(self) -> None:
        ng1 = NoiseGenerator(
            sigma=1.0, distribution="ar1", rng=make_rng(), phi=0.7
        )
        ng2 = NoiseGenerator(
            sigma=1.0, distribution="ar1", rng=make_rng(), phi=0.7
        )
        seq1 = [ng1.sample() for _ in range(200)]
        seq2 = [ng2.sample() for _ in range(200)]
        assert seq1 == seq2


# ---------------------------------------------------------------------------
# from_config factory
# ---------------------------------------------------------------------------


class TestFromConfig:
    """NoiseGenerator.from_config maps config field names."""

    def test_gaussian_from_config(self) -> None:
        ng = NoiseGenerator.from_config(
            sigma=2.0, noise_type="gaussian", rng=make_rng()
        )
        assert ng.distribution == "gaussian"
        assert ng.sigma == 2.0

    def test_student_t_from_config(self) -> None:
        ng = NoiseGenerator.from_config(
            sigma=1.0, noise_type="student_t", rng=make_rng(), noise_df=5.0
        )
        assert ng.distribution == "student_t"

    def test_ar1_from_config(self) -> None:
        ng = NoiseGenerator.from_config(
            sigma=1.0, noise_type="ar1", rng=make_rng(), noise_phi=0.7
        )
        assert ng.distribution == "ar1"

    def test_speed_dependent_from_config(self) -> None:
        ng = NoiseGenerator.from_config(
            sigma=0.0,
            noise_type="gaussian",
            rng=make_rng(),
            sigma_base=0.2,
            sigma_scale=0.015,
        )
        assert ng.effective_sigma(parent_value=100.0) == pytest.approx(1.7)


# ---------------------------------------------------------------------------
# CholeskyCorrelator -- construction
# ---------------------------------------------------------------------------


class TestCholeskyConstruction:
    """CholeskyCorrelator validation."""

    def test_identity_matrix(self) -> None:
        R = np.eye(3)
        cc = CholeskyCorrelator(R)
        assert cc.n == 3
        np.testing.assert_array_almost_equal(cc.L, np.eye(3))

    def test_valid_correlation_matrix(self) -> None:
        R = np.array([
            [1.0, 0.2, 0.15],
            [0.2, 1.0, 0.2],
            [0.15, 0.2, 1.0],
        ])
        cc = CholeskyCorrelator(R)
        assert cc.n == 3
        # L @ L^T should reconstruct R
        np.testing.assert_array_almost_equal(cc.L @ cc.L.T, R)

    def test_non_square_rejected(self) -> None:
        with pytest.raises(ValueError, match="square"):
            CholeskyCorrelator(np.array([[1.0, 0.5], [0.5, 1.0], [0.1, 0.1]]))

    def test_non_symmetric_rejected(self) -> None:
        R = np.array([[1.0, 0.5], [0.3, 1.0]])
        with pytest.raises(ValueError, match="symmetric"):
            CholeskyCorrelator(R)

    def test_non_unit_diagonal_rejected(self) -> None:
        R = np.array([[2.0, 0.5], [0.5, 1.0]])
        with pytest.raises(ValueError, match=r"diagonal must be 1.0"):
            CholeskyCorrelator(R)

    def test_not_positive_definite_rejected(self) -> None:
        R = np.array([[1.0, 1.5], [1.5, 1.0]])
        with pytest.raises(np.linalg.LinAlgError):
            CholeskyCorrelator(R)

    def test_2x2_correlation(self) -> None:
        R = np.array([[1.0, 0.8], [0.8, 1.0]])
        cc = CholeskyCorrelator(R)
        assert cc.n == 2
        np.testing.assert_array_almost_equal(cc.L @ cc.L.T, R)


# ---------------------------------------------------------------------------
# CholeskyCorrelator -- correlation properties
# ---------------------------------------------------------------------------


class TestCholeskyCorrelation:
    """Verify correlations match specification over many samples."""

    def test_vibration_correlations(self) -> None:
        """PRD 4.3.1: vibration axes R = [[1, 0.2, 0.15], [0.2, 1, 0.2], [0.15, 0.2, 1]]."""
        R = np.array([
            [1.0, 0.2, 0.15],
            [0.2, 1.0, 0.2],
            [0.15, 0.2, 1.0],
        ])
        cc = CholeskyCorrelator(R)
        rng = make_rng()

        samples = np.array([cc.correlate(rng.standard_normal(3)) for _ in range(N_SAMPLES)])

        # Empirical correlation matrix
        empirical_R = np.corrcoef(samples.T)
        np.testing.assert_array_almost_equal(empirical_R, R, decimal=1)

    def test_dryer_zone_correlations(self) -> None:
        """PRD 4.3.1: dryer zones R = [[1, 0.1, 0.02], [0.1, 1, 0.1], [0.02, 0.1, 1]]."""
        R = np.array([
            [1.0, 0.1, 0.02],
            [0.1, 1.0, 0.1],
            [0.02, 0.1, 1.0],
        ])
        cc = CholeskyCorrelator(R)
        rng = make_rng()

        samples = np.array([cc.correlate(rng.standard_normal(3)) for _ in range(N_SAMPLES)])

        empirical_R = np.corrcoef(samples.T)
        np.testing.assert_array_almost_equal(empirical_R, R, decimal=1)

    def test_identity_produces_uncorrelated(self) -> None:
        cc = CholeskyCorrelator(np.eye(3))
        rng = make_rng()

        samples = np.array([cc.correlate(rng.standard_normal(3)) for _ in range(N_SAMPLES)])

        empirical_R = np.corrcoef(samples.T)
        # Off-diagonal should be near 0
        off_diag = empirical_R[np.triu_indices(3, k=1)]
        assert np.all(np.abs(off_diag) < 0.05)

    def test_wrong_sample_size_rejected(self) -> None:
        cc = CholeskyCorrelator(np.eye(3))
        with pytest.raises(ValueError, match="Expected 3"):
            cc.correlate(np.array([1.0, 2.0]))

    def test_unit_variance_preserved(self) -> None:
        """Correlated samples should still have unit variance."""
        R = np.array([
            [1.0, 0.5, 0.3],
            [0.5, 1.0, 0.4],
            [0.3, 0.4, 1.0],
        ])
        cc = CholeskyCorrelator(R)
        rng = make_rng()

        samples = np.array([cc.correlate(rng.standard_normal(3)) for _ in range(N_SAMPLES)])

        for i in range(3):
            assert abs(np.std(samples[:, i]) - 1.0) < 0.05


# ---------------------------------------------------------------------------
# CholeskyCorrelator -- generate_correlated convenience method
# ---------------------------------------------------------------------------


class TestGenerateCorrelated:
    """Test the full pipeline: generate + correlate + scale."""

    def test_without_sigmas(self) -> None:
        R = np.array([[1.0, 0.5], [0.5, 1.0]])
        cc = CholeskyCorrelator(R)
        rng = make_rng()
        result = cc.generate_correlated(rng)
        assert result.shape == (2,)

    def test_with_sigmas(self) -> None:
        R = np.array([[1.0, 0.5], [0.5, 1.0]])
        cc = CholeskyCorrelator(R)
        rng = make_rng()
        sigmas = np.array([2.0, 3.0])
        result = cc.generate_correlated(rng, sigmas=sigmas)
        assert result.shape == (2,)

    def test_sigma_scaling_preserves_correlation(self) -> None:
        """PRD 4.3.1: Scaling after correlation preserves correlation coefficients."""
        R = np.array([[1.0, 0.6], [0.6, 1.0]])
        cc = CholeskyCorrelator(R)
        rng = make_rng()
        sigmas = np.array([2.0, 5.0])

        samples = np.array(
            [cc.generate_correlated(rng, sigmas=sigmas) for _ in range(N_SAMPLES)]
        )

        empirical_R = np.corrcoef(samples.T)
        # Correlation should still be ~0.6 despite different sigmas
        assert abs(empirical_R[0, 1] - 0.6) < 0.05

    def test_sigma_scaling_changes_variance(self) -> None:
        """Variance should be sigma^2 after scaling."""
        R = np.eye(2)
        cc = CholeskyCorrelator(R)
        rng = make_rng()
        sigmas = np.array([3.0, 7.0])

        samples = np.array(
            [cc.generate_correlated(rng, sigmas=sigmas) for _ in range(N_SAMPLES)]
        )

        assert abs(np.std(samples[:, 0]) - 3.0) < 0.2
        assert abs(np.std(samples[:, 1]) - 7.0) < 0.5

    def test_wrong_sigma_shape_rejected(self) -> None:
        cc = CholeskyCorrelator(np.eye(3))
        rng = make_rng()
        with pytest.raises(ValueError, match="sigmas must have shape"):
            cc.generate_correlated(rng, sigmas=np.array([1.0, 2.0]))

    def test_deterministic_with_same_seed(self) -> None:
        R = np.array([[1.0, 0.5], [0.5, 1.0]])
        cc = CholeskyCorrelator(R)
        sigmas = np.array([2.0, 3.0])

        r1 = cc.generate_correlated(make_rng(), sigmas=sigmas)
        r2 = cc.generate_correlated(make_rng(), sigmas=sigmas)
        np.testing.assert_array_equal(r1, r2)


# ---------------------------------------------------------------------------
# SignalModel ABC
# ---------------------------------------------------------------------------


class TestSignalModelABC:
    """Verify the ABC contract."""

    def test_cannot_instantiate_directly(self) -> None:
        from factory_simulator.models.base import SignalModel

        with pytest.raises(TypeError):
            SignalModel(params={}, rng=make_rng())  # type: ignore[abstract]

    def test_concrete_subclass_works(self) -> None:
        from factory_simulator.models.base import SignalModel

        class DummyModel(SignalModel):
            def generate(self, sim_time: float, dt: float) -> float:
                return 42.0

        m = DummyModel(params={"target": 42}, rng=make_rng())
        assert m.generate(0.0, 0.1) == 42.0

    def test_reset_is_noop_by_default(self) -> None:
        from factory_simulator.models.base import SignalModel

        class DummyModel(SignalModel):
            def generate(self, sim_time: float, dt: float) -> float:
                return 0.0

        m = DummyModel(params={}, rng=make_rng())
        m.reset()  # should not raise


# ---------------------------------------------------------------------------
# Package imports
# ---------------------------------------------------------------------------


class TestPackageImports:
    """Verify the models package exports the right symbols."""

    def test_imports(self) -> None:
        from factory_simulator.models import (
            CholeskyCorrelator,
            NoiseGenerator,
            SignalModel,
        )

        assert CholeskyCorrelator is not None
        assert NoiseGenerator is not None
        assert SignalModel is not None
