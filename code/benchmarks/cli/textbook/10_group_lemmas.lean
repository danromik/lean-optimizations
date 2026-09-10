import Mathlib

variable {G : Type*} [Group G]

theorem mul_inv_rev' (a b : G) : (a * b)⁻¹ = b⁻¹ * a⁻¹ := by simp

theorem conj_cancel (a b : G) : a * b * b⁻¹ * a⁻¹ = 1 := by group

theorem conj_pow' (a b : G) (n : ℕ) : (a * b * a⁻¹) ^ n = a * b ^ n * a⁻¹ := by
  induction n with
  | zero => simp
  | succ n ih => rw [pow_succ, ih, pow_succ]; group

theorem comm_abel {A : Type*} [AddCommGroup A] (x y z : A) : x + y - z + (z - x) = y := by abel

theorem orderOf_dvd_card_fintype {H : Type*} [Group H] [Fintype H] (x : H) :
    orderOf x ∣ Fintype.card H :=
  orderOf_dvd_card
