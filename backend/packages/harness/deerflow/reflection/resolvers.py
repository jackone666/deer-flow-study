"""动态模块解析器。

实现两个高层入口：
- :func:`resolve_variable` 解析 ``module:attr`` 形式的路径并返回对应变量；
- :func:`resolve_class` 在此基础上进一步校验结果是类，并可选择校验父类。

通过 ``MODULE_TO_PACKAGE_HINTS`` 已知集成包名，能在依赖缺失时给出可操作
的安装提示。
"""

from importlib import import_module

MODULE_TO_PACKAGE_HINTS = {
    "langchain_google_genai": "langchain-google-genai",
    "langchain_anthropic": "langchain-anthropic",
    "langchain_openai": "langchain-openai",
    "langchain_deepseek": "langchain-deepseek",
}


def _build_missing_dependency_hint(module_path: str, err: ImportError) -> str:
    """当模块导入失败时构建一条可操作的安装提示。

    对于已知集成包（如 ``langchain_openai``），即使 ``ImportError`` 实际
    来自某个传递依赖，也会优先返回正确的安装包名。

    Args:
        module_path: 触发 ``ImportError`` 的目标模块路径。
        err: 抛出的 ``ImportError`` 实例，用于读取 ``err.name``。

    Returns:
        形如 ``"Missing dependency 'xxx'. Install it with `uv add xxx` ..."`` 的提示。
    """
    module_root = module_path.split(".", 1)[0]
    missing_module = getattr(err, "name", None) or module_root

    # Prefer provider package hints for known integrations, even when the import
    # error is triggered by a transitive dependency (e.g. `google`).
    package_name = MODULE_TO_PACKAGE_HINTS.get(module_root)
    if package_name is None:
        package_name = MODULE_TO_PACKAGE_HINTS.get(missing_module, missing_module.replace("_", "-"))

    return f"Missing dependency '{missing_module}'. Install it with `uv add {package_name}` (or `pip install {package_name}`), then restart DeerFlow."


def resolve_variable[T](
    variable_path: str,
    expected_type: type[T] | tuple[type, ...] | None = None,
) -> T:
    """从 ``module:attr`` 形式的路径解析出一个变量。

    Args:
        variable_path: 形如
            ``"parent_package.sub_package.module_name:variable_name"`` 的字符串。
        expected_type: 可选类型或类型元组，用于在返回前做 ``isinstance`` 校验。

    Returns:
        解析得到的变量。

    Raises:
        ImportError: 当模块路径不合法或属性不存在时。
        ValueError: 当解析结果不满足 ``expected_type`` 时。
    """
    try:
        module_path, variable_name = variable_path.rsplit(":", 1)
    except ValueError as err:
        raise ImportError(f"{variable_path} doesn't look like a variable path. Example: parent_package_name.sub_package_name.module_name:variable_name") from err

    try:
        module = import_module(module_path)
    except ImportError as err:
        module_root = module_path.split(".", 1)[0]
        err_name = getattr(err, "name", None)
        if isinstance(err, ModuleNotFoundError) or err_name == module_root:
            hint = _build_missing_dependency_hint(module_path, err)
            raise ImportError(f"Could not import module {module_path}. {hint}") from err
        # Preserve the original ImportError message for non-missing-module failures.
        raise ImportError(f"Error importing module {module_path}: {err}") from err

    try:
        variable = getattr(module, variable_name)
    except AttributeError as err:
        raise ImportError(f"Module {module_path} does not define a {variable_name} attribute/class") from err

    # Type validation
    if expected_type is not None:
        if not isinstance(variable, expected_type):
            type_name = expected_type.__name__ if isinstance(expected_type, type) else " or ".join(t.__name__ for t in expected_type)
            raise ValueError(f"{variable_path} is not an instance of {type_name}, got {type(variable).__name__}")

    return variable


def resolve_class[T](class_path: str, base_class: type[T] | None = None) -> type[T]:
    """从模块路径和类名解析出一个类对象。

    Args:
        class_path: 形如 ``"langchain_openai:ChatOpenAI"`` 的字符串。
        base_class: 可选基类；若提供，会校验解析结果是该基类的子类。

    Returns:
        解析得到的类对象。

    Raises:
        ImportError: 当模块路径不合法或属性不存在时。
        ValueError: 当解析结果不是类，或不是 ``base_class`` 的子类时。
    """
    model_class = resolve_variable(class_path, expected_type=type)

    if not isinstance(model_class, type):
        raise ValueError(f"{class_path} is not a valid class")

    if base_class is not None and not issubclass(model_class, base_class):
        raise ValueError(f"{class_path} is not a subclass of {base_class.__name__}")

    return model_class
