from dunders.app import DundersApp
from dunders.fm import associations_loader as L


async def _settle(pilot):
    await pilot.pause()
    await pilot.pause()


async def test_edit_associations_seeds_and_opens_editor(tmp_path):
    app = DundersApp(launch_mode="fm", initial_path=str(tmp_path))
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot)
        assert not L.associations_path().exists()
        app.action_edit_associations()
        await _settle(pilot)
    assert L.associations_path().is_file()  # seeded on first open
