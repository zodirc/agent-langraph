def test_purge_task_remains_deletes_llm_interactions(isolated_stores, monkeypatch):
    from app.config.settings import settings
    from app.runtime.state import create_initial_state, merge_state
    from app.services.llm_interaction_store import get_llm_interaction_store
    from app.services.state_store import get_state_store
    from app.services.task_cleanup import purge_task_remains

    state = merge_state(create_initial_state(), status="COMPLETED")
    task_id = state["task_id"]
    get_state_store().save(state)
    get_llm_interaction_store().record(
        task_id=task_id,
        session_id=task_id,
        purpose="writing",
        system_prompt="s",
        user_content="u",
        response_text="r",
    )
    assert get_llm_interaction_store().count_for_task(task_id) == 1

    result = purge_task_remains(task_id)
    assert result["llm_interactions_removed"] == 1
    assert get_llm_interaction_store().count_for_task(task_id) == 0
