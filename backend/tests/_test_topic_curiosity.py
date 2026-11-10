from backend.app.content.curiosity_topic_engine import CuriosityTopicEngine

engine = CuriosityTopicEngine()
ranked = engine.discover_and_rank_topics()
print(f"Discovered & Ranked {len(ranked)} Topics:")
for idx, t in enumerate(ranked, 1):
    print(f"{idx}. [{t.category_code}: {t.category}] {t.title} -> Score: {t.composite_score}")

winner, best_hook = engine.select_best_topic()
print(f"\nWINNER: {winner.title} (Score: {winner.composite_score})")
print("5 Generated Hooks:")
for h in winner.hook_candidates:
    print(f"  [{h.approach}] Score: {h.composite_score} -> '{h.hook_text}'")
print(f"\nSELECTED HOOK: [{best_hook.approach}] '{best_hook.hook_text}'")
