import Mathlib

theorem modus_ponens_chain (p q r : Prop) (hp : p) (hq : q) (h : p → q → r) : r := by aesop

theorem and_or_distrib' (p q r : Prop) : p ∧ (q ∨ r) ↔ (p ∧ q) ∨ (p ∧ r) := by aesop

theorem set_inter_subset {α : Type*} (s t : Set α) : s ∩ t ⊆ t ∪ s := by
  intro x hx
  aesop

theorem list_mem_append {α : Type*} (a : α) (l₁ l₂ : List α) (h : a ∈ l₁) : a ∈ l₁ ++ l₂ := by
  aesop

theorem exists_of_forall_not {α : Type*} (p : α → Prop) (h : ¬ ∀ x, ¬ p x) : ∃ x, p x := by
  aesop
