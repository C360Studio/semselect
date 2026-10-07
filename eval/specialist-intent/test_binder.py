import unittest

from binder import bind


class BinderTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {'nodes': ['pump-7', 'pump-8'], 'zones': ['zone-A'],
                        'fields': ['temperature', 'pressure']}

    def test_literal_binding(self):
        self.assertEqual(bind('Follow links from "pump-7"', 'path', self.catalog),
                         {'readiness': 'valid_plan', 'options': {'path_intent': True, 'path_start_node': 'pump-7'}, 'executable': True})
        self.assertEqual(bind('Average temperature.', 'avg', self.catalog)['options'],
                         {'aggregation_type': 'avg', 'aggregation_field': 'temperature'})
        self.assertEqual(bind('Devices in zone-A', 'zone', self.catalog)['options'],
                         {'path_intent': True, 'path_predicates': ['located_in'], 'path_start_node': 'zone-A'})

    def test_does_not_guess_or_copy_example_arguments(self):
        for query in ('Follow links from that device', 'Follow links from Pump-7',
                      'Follow links from pump-70', 'Follow links from x/pump-7',
                      'Follow links from pump-7.metric', 'Follow links from pump-7 and pump-8'):
            with self.subTest(query=query):
                self.assertEqual(bind(query, 'path', self.catalog),
                                 {'readiness': 'needs_binding', 'options': {'path_intent': True}, 'executable': False})

    def test_repeated_literal_is_not_ambiguous(self):
        self.assertTrue(bind('pump-7 links; start at pump-7', 'path', self.catalog)['executable'])

    def test_field_and_zone_catalogs_are_separate(self):
        self.assertEqual(bind('Average pressure and temperature', 'avg', self.catalog)['readiness'], 'needs_binding')
        self.assertEqual(bind('Devices in pump-7', 'zone', self.catalog)['readiness'], 'needs_binding')
        self.assertEqual(bind('Follow links from zone-A', 'path', self.catalog)['readiness'], 'needs_binding')

    def test_no_override_and_argument_free_operations(self):
        self.assertEqual(bind('ordinary search', 'no_override', self.catalog),
                         {'readiness': 'no_override', 'options': {}, 'executable': False})
        self.assertEqual(bind('How many devices?', 'count', self.catalog)['options'], {'aggregation_type': 'count'})
        self.assertEqual(bind('Find similar devices', 'similarity', self.catalog)['options'], {'use_embeddings': True})

    def test_validation(self):
        for catalog in ({}, {'nodes': ['a', 'a'], 'zones': [], 'fields': []}, {'nodes': [None], 'zones': [], 'fields': []}):
            with self.subTest(catalog=catalog), self.assertRaises(ValueError):
                bind('test', 'path', catalog)
        with self.assertRaises(ValueError):
            bind('test', 'execute', self.catalog)


if __name__ == '__main__':
    unittest.main()
