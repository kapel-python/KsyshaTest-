import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import db
from http_api import _collect_site_data

class TestWishUX(unittest.TestCase):
    def setUp(self):
        self.user_id = 99901
        self.partner_id = 99902
        self.couple_id = -1
        
        # Clean up database first just in case
        self.tearDown()
        
        # Insert mock users first due to FOREIGN KEY constraints
        db.add_or_update_user(self.user_id, "alice", "Alice", "")
        db.add_or_update_user(self.partner_id, "bob", "Bob", "")
        
        # Create a mock couple
        self.couple_id = db.create_couple(self.user_id, self.partner_id)
        if self.couple_id == -1:
            raise RuntimeError("Failed to create mock couple in database")
            
    def tearDown(self):
        # Clean up wishes, couples, and users
        try:
            with db._get_connection() as conn:
                conn.execute("DELETE FROM wishes WHERE user_id IN (?, ?)", (self.user_id, self.partner_id))
                if self.couple_id != -1:
                    conn.execute("DELETE FROM couples WHERE id = ?", (self.couple_id,))
                conn.execute("DELETE FROM users WHERE user_id IN (?, ?)", (self.user_id, self.partner_id))
                conn.commit()
        except Exception:
            pass

    def test_wish_api_structure_and_isolation(self):
        print("\n🧪 Testing Backend API wishes payload structure and isolation...")
        
        # 1. User 99901 adds a wish in slot 1
        db.add_wish(self.user_id, wish_number=1, content="Alice wish 1")
        # 2. Partner 99902 adds a wish in slot 2
        db.add_wish(self.partner_id, wish_number=2, content="Bob wish 2")
        
        # Let's collect data for self.user_id (99901)
        # Mock visitor ID validation to pass and return self.user_id
        visitor_id_str = str(self.user_id)
        
        with patch('http_api._get_trusted_visitor_id', return_value=visitor_id_str):
            data = _collect_site_data(timezone_id="UTC", visitor_id=visitor_id_str)
            
        wishes_payload = data.get("wishes")
        self.assertIsNotNone(wishes_payload, "Wishes block is missing in API response!")
        
        # Check correct response structure (Proposed API structure)
        self.assertIn("user", wishes_payload, "CRITICAL: wishes.user is missing from response payload!")
        self.assertIn("partner", wishes_payload, "wishes.partner is missing from response payload!")
        self.assertIn("ksusha", wishes_payload, "wishes.ksusha is missing from response payload!")
        
        # Check isolation: wishes.user must contain only user's wishes
        user_wishes = wishes_payload["user"]
        self.assertEqual(len(user_wishes), 1)
        self.assertEqual(user_wishes[0]["wish_number"], 1)
        self.assertEqual(user_wishes[0]["content_html"], "Alice wish 1")
        
        # Check isolation: wishes.partner must contain only partner's wishes
        partner_wishes = wishes_payload["partner"]
        self.assertEqual(len(partner_wishes), 1)
        self.assertEqual(partner_wishes[0]["wish_number"], 2)
        self.assertEqual(partner_wishes[0]["content_html"], "Bob wish 2")
        
        # Check backward compatibility: wishes.ksusha must be equal to wishes.partner
        self.assertEqual(wishes_payload["ksusha"], partner_wishes)
        print("✅ Backend API Structure & Isolation Verified Successfully!")

if __name__ == "__main__":
    unittest.main()
