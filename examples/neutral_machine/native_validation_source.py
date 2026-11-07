"""Original neutral native-engine fixture for validation infrastructure."""
from arcengine import ARCBaseGame,Level,Sprite,GameAction
class NativeEngine(ARCBaseGame):
 def __init__(self):
  super().__init__('neutral-validation',levels=[Level(sprites=[Sprite([[1]],name='actor',x=0,y=0)],grid_size=(8,8),data={'target':1})],available_actions=[1,2])
 def on_set_level(self,level):
  self.actor=level.get_sprites_by_name('actor')[0];self.target=level.get_data('target');self.failed=False
 def step(self):
  if self.action.id==GameAction.RESET:self.handle_reset()
  else:
   self.actor.set_position(self.actor.x+(1 if self.action.id==GameAction.ACTION1 else -1),0)
   if self.actor.x<0:self.failed=True;self.lose()
   elif self.actor.x==self.target:self.next_level()
  self.complete_action()
