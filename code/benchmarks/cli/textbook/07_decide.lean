import Mathlib

theorem fin_lt : ∀ n : Fin 10, n.val < 10 := by decide

theorem bool_and_comm : ∀ a b : Bool, (a && b) = (b && a) := by decide

theorem list_sum_range : (List.range 10).sum = 45 := by decide

theorem no_small_pyth : ¬ ∃ x : Fin 5, ∃ y : Fin 5, x.val ^ 2 + y.val ^ 2 = 3 := by decide

theorem small_fact : Nat.factorial 5 = 120 := by decide
