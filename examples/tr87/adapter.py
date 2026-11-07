"""TR87 Level snapshot adapter extracted from the accepted implementation."""
import copy

import numpy as np
from arcengine import BlockingMode, Level, Sprite
from arcengine.enums import InteractionMode


def level_from_snapshot(snapshot: dict) -> Level:
    sprites = []
    for row in snapshot['sprites']:
        sprite = Sprite(
            pixels=row['pixels'],
            name=row['_name'],
            visible=True,
            collidable=True,
            tags=list(row['_tags']),
            layer=int(row['_layer']),
        )
        sprite._pixels = np.array(row['pixels'], dtype=np.int8)
        sprite._name = row['_name']
        sprite._x = int(row['_x'])
        sprite._y = int(row['_y'])
        sprite._layer = int(row['_layer'])
        sprite._rotation = int(row['rotation'])
        sprite._mirror_ud = bool(row['_mirror_ud'])
        sprite._mirror_lr = bool(row['_mirror_lr'])
        sprite._scale = int(row['_scale'])
        sprite._blocking = BlockingMode(row['_blocking'])
        sprite._interaction = InteractionMode(row['_interaction'])
        sprite._tags = list(row['_tags'])
        sprites.append(sprite)
    return Level(
        sprites=sprites,
        grid_size=tuple(snapshot['grid_size']),
        data=copy.deepcopy(snapshot['data']),
    )
