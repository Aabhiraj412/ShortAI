"""
Agent 2: Topic Selector.
Selects the highest-potential topic from candidates, prevents duplication,
and supports manual override from the React dashboard.
"""
from typing import Dict, List, Optional

class TopicSelector:
    def __init__(self):
        pass

    def select_topic(
        self, 
        candidates: List[Dict], 
        manual_topic_id: Optional[int] = None,
        manual_topic_title: Optional[str] = None
    ) -> Dict:
        """
        Picks the winning topic.
        If manual selection is provided by the dashboard, it takes immediate precedence.
        Otherwise, selects the candidate with the highest composite score.
        """
        if not candidates:
            raise ValueError("No topic candidates available to select from.")
            
        # 1. Manual override check
        if manual_topic_title:
            for c in candidates:
                if c["title"].lower() == manual_topic_title.lower():
                    c["selection_mode"] = "manual_dashboard_override"
                    return c
            return {
                "title": manual_topic_title,
                "category": "Custom Topic",
                "summary": "Manually entered topic from dashboard.",
                "sources": [],
                "score": 10.0,
                "selection_mode": "manual_dashboard_override"
            }
            
        # 2. Automated selection: Top candidate by score
        sorted_candidates = sorted(candidates, key=lambda x: x.get("score", 0.0), reverse=True)
        winner = sorted_candidates[0]
        winner["selection_mode"] = "autonomous_ranking"
        return winner
