def test_version_import():
    import agent_data_toolkit as adt

    assert hasattr(adt, "__version__")
    assert isinstance(adt.__version__, str)
