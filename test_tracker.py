import copy
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlparse, parse_qs
from unittest.mock import patch
import tracker as t

class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.c = t.load_config(t.ROOT / 'config.json')
        self.s = self.c['searches'][0]
        self.item = t.demo_items()[0]

    def score(self, item=None, mode='shipped'):
        return t.evaluate(item or self.item, self.s, self.c, mode)

    def test_landed_cost_and_discount(self):
        r = self.score()
        self.assertEqual(r['total'], 320)
        self.assertEqual(r['discount'], 24.7)
        self.assertTrue(r['good'])

    def test_unknown_shipping_not_free(self):
        self.item['shippingOptions'] = []
        r = self.score()
        self.assertIsNone(r['total'])
        self.assertFalse(r['good'])
        self.assertEqual(self.score(mode='pickup')['total'], 285)

    def test_shipping_over_budget(self):
        self.item['shippingOptions'][0]['shippingCost']['value'] = '100'
        self.assertIsNone(self.score())

    def test_wrong_models_accessories_and_repair_excluded(self):
        for title in ['Yamaha P-125a piano', 'Yamaha P-1250 piano', 'Yamaha P-125 cover', 'Yamaha P-125 for parts', 'Adapter for Yamaha P-125']:
            self.item['title'] = title
            self.assertIsNone(self.score(), title)

    def test_auction_currency_condition(self):
        for key, value in [('buyingOptions',['AUCTION']),('conditionId','7000'),('price',{'value':'100','currency':'EUR'})]:
            i=copy.deepcopy(self.item)
            i[key]=value
            self.assertIsNone(self.score(i))

    def test_risky_seller_and_extreme_discount(self):
        self.item['seller']['feedbackScore']=0
        self.assertFalse(self.score()['good'])
        self.item['seller']['feedbackScore']=100
        self.item['price']['value']='50'
        result = self.score()
        self.assertTrue(result['good'])
        self.assertEqual(result['tier'], 'Exceptional find')
        self.assertIn('Unusually cheap', result['warnings'][0])

    def test_dedup_and_price_drop(self):
        r=self.score()
        self.assertTrue(t.should_alert(r, {}))
        self.assertFalse(t.should_alert(r, {r['id']:320}))
        self.assertFalse(t.should_alert(r, {r['id']:324}))
        self.assertTrue(t.should_alert(r, {r['id']:325}))

    def test_deal_tiers_match_relative_value(self):
        self.assertEqual(t.deal_tier(59.2), 'Exceptional find')
        self.assertEqual(t.deal_tier(40.8), 'Strong value')
        self.assertEqual(t.deal_tier(18.4), 'Good buy')
        self.assertEqual(t.deal_tier(2), 'Fair price')
        self.assertEqual(t.deal_tier(-11), 'Above market')
        self.assertEqual(t.deal_tier(None), 'Needs details')

    def test_api_location_pagination(self):
        api=t.Ebay()
        responses=[{'itemSummaries':[self.item], 'next':'next'}, {'itemSummaries':[]}]
        with patch.object(api,'access_token',return_value='test'), patch.object(api,'request',side_effect=responses) as call:
            result, truncated=api.search(self.s,self.c,'pickup')
            self.assertEqual(len(result),1)
            self.assertFalse(truncated)
            params=parse_qs(urlparse(call.call_args_list[0].args[0]).query)
            self.assertIn('pickupPostalCode:90503',params['filter'][0])
            self.assertIn('pickupRadiusUnit:mi',params['filter'][0])
            self.assertIn('SELLER_ARRANGED_LOCAL_PICKUP',params['filter'][0])
            self.assertEqual(parse_qs(urlparse(call.call_args_list[1].args[0]).query)['offset'],['200'])

    def test_demo_isolation_and_escaping(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            t.run(self.c,t.Ebay(),True,path,False)
            self.assertFalse((path/'alert-state.json').exists())
            self.assertFalse((path/'alerts.jsonl').exists())
            self.assertIn('DEMO', (path/'report.html').read_text())
            r=self.score()
            r['title']='<script>alert(1)</script>'
            t.render([r],self.c,[],False,path/'report.html',[])
            self.assertIn('&lt;script&gt;', (path/'report.html').read_text())

    def test_live_alert_persistence_and_failure(self):
        c=copy.deepcopy(self.c)
        c['searches']=[self.s]
        c['mode']='shipped'
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            api=t.Ebay()
            with patch.object(api,'search',return_value=([self.item],False)):
                t.run(c,api,False,path,False)
                t.run(c,api,False,path,False)
            self.assertEqual(len((path/'alerts.jsonl').read_text().splitlines()),1)
            with patch.object(api,'search',side_effect=RuntimeError('eBay HTTP 429. Wait.')):
                _, issues=t.run(c,api,False,path,False)
            self.assertTrue(issues)
            self.assertEqual(json.loads((path/'results.json').read_text())['items'],[])
            self.assertIn('HTTP 429', (path/'report.html').read_text())

    def test_invalid_config(self):
        self.c['interval_minutes']=0
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'config.json'
            p.write_text(json.dumps(self.c))
            with self.assertRaises(AssertionError):
                t.load_config(p)

    def test_cross_market_links_and_calculator(self):
        links = dict(t.marketplace_links(self.s, self.c))
        self.assertEqual(set(links), {'Facebook', 'OfferUp', 'Craigslist LA', 'Craigslist OC', 'Reverb', 'Guitar Center', 'Music Go Round', 'Sweetwater', 'Mercari', 'ShopGoodwill'})
        self.assertIn('Yamaha+P-125', links['Craigslist LA'])
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / 'report.html'
            t.render([], self.c, [], True, report, [])
            page = report.read_text()
            self.assertIn('Score any listing', page)
            self.assertIn('The calculation stays in this browser', page)
            self.assertIn('Facebook', page)
            self.assertNotIn('http-equiv="refresh"', page)
            self.assertNotIn('ZIP 90503', page)

if __name__=='__main__':
    unittest.main()
