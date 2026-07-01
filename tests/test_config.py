import unittest

import config
import data_sorter
import email_extractor
import statement_extractor
import system_logger
import yfinance_extractor


class ConfigTest(unittest.TestCase):
    def test_module_aliases_point_to_shared_config_values(self):
        self.assertEqual(email_extractor.OUTPUT_COLUMNS, config.EMAIL_OUTPUT_COLUMNS)
        self.assertEqual(email_extractor.WEALTHSIMPLE_SENDERS, config.WEALTHSIMPLE_SENDERS)
        self.assertEqual(email_extractor.INTERAC_SENDER, config.INTERAC_SENDER)
        self.assertEqual(email_extractor.INTERAC_SUBJECT_PATTERN.pattern, config.INTERAC_SUBJECT_PATTERN.pattern)

        self.assertEqual(statement_extractor.OUTPUT_COLUMNS, config.STATEMENT_OUTPUT_COLUMNS)
        self.assertEqual(statement_extractor.DEFAULT_DATA_FOLDER, config.DEFAULT_DATA_FOLDER)
        self.assertEqual(statement_extractor.DEFAULT_EXPORT_FOLDER, config.DEFAULT_EXPORT_FOLDER)

        self.assertEqual(data_sorter.SOURCE_PREFIX, config.SOURCE_PREFIX)
        self.assertEqual(data_sorter.PROCESSED_FOLDER_NAME, config.PROCESSED_FOLDER_NAME)
        self.assertEqual(data_sorter.REQUIRED_COLUMNS, config.REQUIRED_COLUMNS)
        self.assertEqual(data_sorter.DROP_COLUMNS, config.DROP_COLUMNS)
        self.assertEqual(data_sorter.COLUMN_RENAMES, config.COLUMN_RENAMES)
        self.assertEqual(data_sorter.ACTIVITY_TYPE_MAPPING, config.ACTIVITY_TYPE_MAPPING)

        self.assertEqual(system_logger.LOG_PATH, config.LOG_PATH)
        self.assertEqual(system_logger.LOG_FORMAT, config.LOG_FORMAT)
        self.assertEqual(system_logger.DATE_FORMAT, config.DATE_FORMAT)

        self.assertEqual(yfinance_extractor.STOCK_INFO_COLUMNS, config.YFINANCE_STOCK_INFO_COLUMNS)
        self.assertEqual(yfinance_extractor.ETF_INFO_COLUMNS, config.YFINANCE_ETF_INFO_COLUMNS)
        self.assertEqual(yfinance_extractor.HISTORY_COLUMNS, config.YFINANCE_HISTORY_COLUMNS)
