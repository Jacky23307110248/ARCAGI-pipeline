"""Synthetic reference environment, independent of the candidate producer."""
class ReferenceEngine:
    def __init__(self, target):
        self.target = target
        self.state = {"items": []}
    def step(self, action):
        if not isinstance(action, dict) or set(action) != {"write"}: return False
        self.state["items"].append(action["write"])
        return True
    def won(self): return self.state["items"] == [self.target]
