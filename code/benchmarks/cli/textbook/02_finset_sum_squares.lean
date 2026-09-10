import Mathlib

/-- Sum of squares formula. -/
theorem six_mul_sum_sq (n : ℕ) :
    6 * (∑ i ∈ Finset.range (n + 1), i ^ 2) = n * (n + 1) * (2 * n + 1) := by
  induction n with
  | zero => simp
  | succ n ih =>
    rw [Finset.sum_range_succ, mul_add, ih]
    ring

/-- Geometric sum in a commutative ring, telescoped. -/
theorem geom_sum_mul_sub (R : Type*) [CommRing R] (x : R) (n : ℕ) :
    (∑ i ∈ Finset.range n, x ^ i) * (x - 1) = x ^ n - 1 := by
  induction n with
  | zero => simp
  | succ n ih =>
    rw [Finset.sum_range_succ, add_mul, ih]
    ring
