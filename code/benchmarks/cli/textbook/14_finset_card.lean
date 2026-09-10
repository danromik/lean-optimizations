import Mathlib

theorem card_range' (n : ℕ) : (Finset.range n).card = n := by simp

theorem card_powerset_range (n : ℕ) : (Finset.range n).powerset.card = 2 ^ n := by simp

theorem card_image_succ (n : ℕ) : ((Finset.range n).image (· + 1)).card = n := by
  rw [Finset.card_image_of_injective _ (add_left_injective 1)]
  simp

theorem card_union_disjoint' (s t : Finset ℕ) (h : Disjoint s t) :
    (s ∪ t).card = s.card + t.card := by
  rw [Finset.card_union_of_disjoint h]

/-- Pigeonhole: a map from a bigger finite type to a smaller one is not injective. -/
theorem not_injective_of_card_lt {α β : Type*} [Fintype α] [Fintype β]
    (h : Fintype.card β < Fintype.card α) (f : α → β) : ¬ Function.Injective f :=
  fun hf => absurd (Fintype.card_le_of_injective f hf) (not_le.mpr h)

theorem card_image_double (n : ℕ) : ((Finset.range n).image (fun k => 2 * k)).card = n := by
  rw [Finset.card_image_of_injective _ (fun a b h => by simpa using h)]
  simp

theorem card_sdiff_range (n m : ℕ) (h : m ≤ n) :
    (Finset.range n \ Finset.range m).card = n - m := by
  rw [Finset.card_sdiff, Finset.inter_eq_left.mpr (Finset.range_mono h)]
  simp
