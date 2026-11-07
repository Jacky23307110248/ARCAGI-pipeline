"""Small original engine for offline generated-game contract checks."""
import copy
import time

class Engine:
    def __init__(self, ignore_install=False, unit_failure=False, reject=False):
        self.configs = [{"start": 0, "target": 1}]
        self.ignore_install, self.unit_failure, self.reject = ignore_install, unit_failure, reject
        self.reset()
    def configuration(self): return copy.deepcopy(self.configs)
    def install_configuration(self, configs):
        if not self.ignore_install: self.configs = copy.deepcopy(configs)
    def reset(self): self.index = self.completed = 0; self.failed = False; self.select(0)
    def select(self, index): self.index = index; self.completed = index; self.value = self.configs[index]["start"]
    def step(self, action):
        if isinstance(action,dict) and 'wait_seconds' in action:time.sleep(action['wait_seconds']);return
        if self.reject: return
        self.value += action["delta"] if isinstance(action, dict) else (1 if action == "forward" else -1)
        if self.value < 0: self.failed = True; return
        if self.value == self.configs[self.index]["target"]:
            self.completed = self.index + 1
            if self.index + 1 < len(self.configs):
                self.index += 1; self.value = self.configs[self.index]["start"]
    def observe(self): return {"value": self.value, "completed": self.completed, "position": self.index, "failed": self.failed}
    def novelty_observe(self): return {"value": self.value}
    def position(self): return self.index
    def goal(self, goal):
        c = self.configs[self.index]
        return self.completed >= goal["completed"] or (self.value == c["target"] and goal["completed"] == self.index + 1)
    def complete(self): return self.completed == len(self.configs) and not self.unit_failure
