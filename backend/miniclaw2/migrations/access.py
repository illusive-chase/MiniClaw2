from __future__ import annotations

import inspect
from functools import wraps
from typing import Any, Callable, TypeVar, cast

from .coordinator import open_storage

Function = TypeVar("Function", bound=Callable[..., Any])


def storage_function(function: Function) -> Function:
    signature = inspect.signature(function)

    @wraps(function)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        from ..global_config import miniclaw_home

        arguments = signature.bind(*args, **kwargs).arguments
        store = arguments.get("store")
        if store is None and arguments.get("registry") is not None:
            store = arguments["registry"].store
        root = miniclaw_home(arguments.get("store_root") or (store.root if store is not None else None))
        storage = open_storage(root)
        with storage.access():
            return function(*args, **kwargs)

    return cast(Function, guarded)


def storage_methods(cls: type) -> type:
    def guard(function: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(function)
        def guarded(self: Any, *args: Any, **kwargs: Any) -> Any:
            with self.coordinator.access():
                return function(self, *args, **kwargs)

        return guarded

    for name, function in list(vars(cls).items()):
        if not name.startswith("_") and name != "assert_writable" and inspect.isfunction(function):
            setattr(cls, name, guard(function))
    return cls
