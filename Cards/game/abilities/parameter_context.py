from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..enums import *
from immutabledict import immutabledict

@dataclass
class ParameterContext:

    x_variables: immutabledict[XVariable, int] = field(default_factory=immutabledict)