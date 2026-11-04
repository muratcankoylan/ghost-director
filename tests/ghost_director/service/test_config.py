from unittest.mock import patch

from ghost_director.config import load_settings


def test_default_config_does_not_search_parent_workspaces(tmp_path):
    repo = tmp_path / 'workspace' / 'app'
    repo.mkdir(parents=True)
    (tmp_path / '.env.local').write_text('MODEL_API_KEY=unrelated-private-config\n')
    with patch('ghost_director.config.REPO', repo), patch.dict('os.environ', {}, clear=True):
        assert load_settings().meta_key == ''
        assert load_settings(tmp_path / '.env.local').meta_key == 'unrelated-private-config'


def test_external_context_setting_is_explicit_and_server_side(tmp_path):
    env = tmp_path / '.env.local'
    context = tmp_path / 'trusted-studio'
    env.write_text(f'GHOST_STUDIO_ROOT={context}\nSTUDIO_ALLOW_LIVE_CALLS=0\n')
    with patch.dict('os.environ', {}, clear=True):
        settings = load_settings(env)
    assert settings.studio_root == context
    assert settings.allow_live is False
