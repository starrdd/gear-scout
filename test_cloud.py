import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import cloud_run as c
import tracker

class CloudTests(unittest.TestCase):
    def test_exceptional_issue_payload(self):
        row = {'title':'AKG C214','total':100.0,'discount':59.2,'typical':245,'url':'https://www.ebay.com/itm/1','condition':'Used','seller':'seller','location':'LA'}
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = b'{}'
        with patch.dict(os.environ, {'GITHUB_REPOSITORY':'starrdd/gear-scout','GITHUB_REPOSITORY_OWNER':'starrdd','GH_TOKEN':'test'}), patch('urllib.request.urlopen', return_value=response) as call:
            c.github_issue(row)
        request = call.call_args.args[0]
        payload = json.loads(request.data)
        self.assertIn('Exceptional find', payload['title'])
        self.assertEqual(payload['assignees'], ['starrdd'])

    def test_demo_no_network_or_state(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'config.json').write_text((tracker.ROOT/'config.json').read_text())
            with patch.dict(os.environ, {'GEAR_SCOUT_MODE':'demo'}, clear=True), patch.object(tracker,'ROOT',root), patch.object(c,'github_state') as api:
                self.assertEqual(c.main(),0)
                api.assert_not_called()
            self.assertIn('DEMO', (root/'cloud-output/summary.md').read_text())
            self.assertFalse((root/'cloud-output/alert-state.json').exists())

    def test_live_requires_secrets(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'GEAR_SCOUT_MODE':'live'}, clear=True), patch.object(tracker,'ROOT',Path(d)):
            with self.assertRaisesRegex(ValueError,'both eBay secrets'):
                c.main()

    def test_invalid_mode(self):
        with patch.dict(os.environ, {'GEAR_SCOUT_MODE':'typo'}, clear=True):
            with self.assertRaises(ValueError):
                c.main()

    def test_live_restores_state_and_no_duplicate_commit(self):
        import base64
        config=tracker.load_config(tracker.ROOT/'config.json')
        config['searches']=config['searches'][:1]
        config['mode']='shipped'
        state={'demo-1':320}
        blob={'sha':'old-sha','content':base64.b64encode(json.dumps(state).encode()).decode()}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'config.json').write_text(json.dumps(config))
            with patch.dict(os.environ, {'GEAR_SCOUT_MODE':'live','EBAY_CLIENT_ID':'test','EBAY_CLIENT_SECRET':'test'}, clear=True), patch.object(tracker,'ROOT',root), patch.object(c,'github_state',return_value=blob) as api, patch.object(tracker.Ebay,'search',return_value=([tracker.demo_items()[0]],False)):
                self.assertEqual(c.main(),0)
                self.assertEqual(api.call_count,1)
            self.assertFalse((root/'cloud-output/alerts.jsonl').exists())

if __name__=='__main__': unittest.main()
