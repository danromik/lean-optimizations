import Mathlib

variable {R : Type*} [CommRing R]

theorem sq_expand (a b : R) : (a + b) ^ 2 = a ^ 2 + 2 * a * b + b ^ 2 := by ring

theorem diff_of_squares (a b : R) : (a + b) * (a - b) = a ^ 2 - b ^ 2 := by ring

theorem cube_expand (a b : R) : (a + b) ^ 3 = a ^ 3 + 3 * a ^ 2 * b + 3 * a * b ^ 2 + b ^ 3 := by ring

theorem sophie_germain (a b : R) :
    a ^ 4 + 4 * b ^ 4 = (a ^ 2 + 2 * b ^ 2 + 2 * a * b) * (a ^ 2 + 2 * b ^ 2 - 2 * a * b) := by ring

theorem field_identity {K : Type*} [Field K] (x : K) (hx : x ≠ 0) (hx1 : x ≠ 1) :
    1 / x + 1 / (1 - x) = 1 / (x * (1 - x)) := by
  have h1 : (1 : K) - x ≠ 0 := sub_ne_zero.mpr (Ne.symm hx1)
  field_simp
  ring
