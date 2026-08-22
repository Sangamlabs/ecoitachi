"""Tests for Global Battle Items, Inventory, and Store services."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import global_battle as gb_db
from database.mongo import mongo
from services.global_battle import items as items_service, inventory as inventory_service, store as store_service, currency as currency_service
from services import transaction as tx_service
from services.economy import InsufficientBalance


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, modified_count, upserted_id=None):
        self.modified_count = modified_count
        self.upserted_id = upserted_id


class _FakeCollection:
    def __init__(self):
        self.docs = {}

    def _match_query(self, doc, query):
        """Check if a document matches a query with basic operator support."""
        for k, v in query.items():
            if k not in doc:
                return False
            doc_val = doc.get(k)
            if isinstance(v, dict):
                # Handle operators
                for op, op_val in v.items():
                    if op == "$gt":
                        if not (doc_val > op_val):
                            return False
                    elif op == "$gte":
                        if not (doc_val >= op_val):
                            return False
                    elif op == "$lt":
                        if not (doc_val < op_val):
                            return False
                    elif op == "$lte":
                        if not (doc_val <= op_val):
                            return False
                    elif op == "$in":
                        if doc_val not in op_val:
                            return False
                    elif op == "$nin":
                        if doc_val in op_val:
                            return False
                    elif op == "$ne":
                        if doc_val == op_val:
                            return False
                        else:
                            # $ne with non-scalar value
                            return False
                    else:
                        # Unknown operator
                        return False
                    # If we get here, the operator matched
                    continue
                else:
                    # Exact match
                    if doc_val != v:
                        return False
        return True

    async def find_one(self, query, projection=None):
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in query.items()):
                if projection:
                    filtered = {}
                    for k, v in projection.items():
                        if v == 1:
                            filtered[k] = doc.get(k)
                        elif v == 0:
                            continue
                        else:
                            filtered[k] = doc.get(k)
                        if projection.get("_id") != 0 and "_id" in doc:
                            filtered["_id"] = doc["_id"]
                        return filtered
                return doc
        return None

    async def find_one_and_update(self, filt, update, upsert=False, return_document=False):
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in filt.items()):
                if "$inc" in update:
                    for k, v in update["$inc"].items():
                        doc[k] = doc.get(k, 0) + v
                if "$set" in update:
                    doc.update(update["$set"])
                if "$setOnInsert" in update:
                    pass
                if return_document:
                    return doc
                return doc
        if upsert:
            new_doc = dict(update.get("$set", {}))
            if "$setOnInsert" in update:
                new_doc.update(update["$setOnInsert"])
            new_doc.update(filt)
            if "$inc" in update:
                for k, v in update["$inc"].items():
                    new_doc[k] = new_doc.get(k, 0) + v
            new_doc_id = id(new_doc)
            self.docs[new_doc_id] = new_doc
            if return_document:
                return new_doc
            return new_doc
        return None

    async def update_one(self, filt, update, upsert=False):
        existing = None
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in filt.items()):
                existing = doc
                break

        if existing:
            if "$inc" in update:
                for k, v in update["$inc"].items():
                    existing[k] = existing.get(k, 0) + v
            if "$set" in update:
                existing.update(update["$set"])
            return _Result(1)
        if upsert:
            new_doc = dict(filt)
            if "$setOnInsert" in update:
                new_doc.update(update["$setOnInsert"])
            if "$set" in update:
                new_doc.update(update["$set"])
            if "$inc" in update:
                for k, v in update["$inc"].items():
                    new_doc[k] = new_doc.get(k, 0) + v
            new_doc_id = id(new_doc)
            self.docs[new_doc_id] = new_doc
            return _Result(1, upserted_id=new_doc_id)
        return _Result(0)

    async def insert_one(self, doc):
        self.docs[id(doc)] = doc

    async def delete_one(self, filt):
        to_delete = [k for k, v in self.docs.items() if all(v.get(kk) == vv for kk, vv in filt.items())]
        for k in to_delete:
            del self.docs[k]
        return _Result(len(to_delete))

    def find(self, query=None):
        match_query = self._match_query
        
        class _Cursor:
            def __init__(self, docs):
                self._docs = [doc for doc in docs if not query or match_query(doc, query)]

            def sort(self, key, direction=1):
                reverse = direction == -1
                if isinstance(key, str):
                    self._docs.sort(key=lambda d: d.get(key, ""), reverse=reverse)
                else:
                    self._docs.sort(key=lambda d: d.get(key[0], ""), reverse=reverse)
                return self

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._docs:
                    return self._docs.pop(0)
                raise StopAsyncIteration

        return _Cursor(list(self.docs.values()))

    async def count_documents(self, query):
        count = 0
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in query.items()):
                count += 1
        return count

    async def delete_one(self, filt):
        to_delete = [k for k, v in self.docs.items() if all(v.get(kk) == vv for kk, vv in filt.items())]
        for k in to_delete:
            del self.docs[k]
        return _Result(len(to_delete))


class _FakeDb:
    def __init__(self):
        self._collections = {
            "global_missions": _FakeCollection(),
            "global_mission_progress": _FakeCollection(),
            "global_profiles": _FakeCollection(),
            "global_items": _FakeCollection(),
            "global_inventory": _FakeCollection(),
            "global_equipment": _FakeCollection(),
            "global_special_weapons": _FakeCollection(),
            "users": _FakeCollection(),
            "counters": _FakeCollection(),
            "settings": _FakeCollection(),
            "transactions": _FakeCollection(),
            "admins": _FakeCollection(),
        }

    def __getitem__(self, name):
        return self._collections[name]


class _Result:
    def __init__(self, modified_count, upserted_id=None):
        self.modified_count = modified_count
        self.upserted_id = upserted_id
    
    @property
    def deleted_count(self):
        return self.modified_count


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def event_loop():
    """Create a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def clean_db(monkeypatch):
    db = _FakeDb()
    monkeypatch.setattr(mongo, "db", db)
    import asyncio
    asyncio.run(gb_db.init_missions())
    asyncio.run(db["settings"].insert_one({"key": "global", "starting_balance": 50000, "global_battle": {"rs_per_gb": 100, "base_hp": 100, "hp_per_stat": 5, "melee_scaling": 1.0, "ability_scaling": 1.0, "durability_scaling": 1.0}}))
    yield


# ---------------------------------------------------------------------------
# Helper functions for tests
# ---------------------------------------------------------------------------

async def setup_item(item_id: str, item_type: str = "weapon", **kwargs):
    """Create a test item and return its ID."""
    if item_type == "weapon":
        defaults = {
            "name": "Test Weapon",
            "rarity": "common",
            "damage": 20,
            "accuracy": 0.8,
            "fire_rate": 5,
            "ammo": 30,
            "durability": 100,
            "price": 1000,
            "description": "Test",
            "active": True,
        }
        defaults.update(kwargs)
        return await items_service.create_weapon(item_id=item_id, **defaults)
    elif item_type == "armor":
        defaults = {
            "name": "Test Armor",
            "rarity": "common",
            "defense": 30,
            "base_durability": 100,
            "price": 2000,
            "description": "Test",
            "active": True,
        }
        defaults.update(kwargs)
        return await items_service.create_armor(item_id=item_id, **defaults)
    elif item_type == "special":
        defaults = {
            "name": "Test Special",
            "description": "Test",
            "damage": 100,
            "effect": "test",
            "active": True,
        }
        defaults.update(kwargs)
        return await items_service.create_special_weapon(special_id=item_id, **defaults)
    raise ValueError(f"Unknown item_type: {item_type}")


async def add_item_to_user(user_id: int, item_id: str, quantity: int = 1, durability_current: int | None = None):
    """Add an item to a user's inventory."""
    return await inventory_service.add_to_inventory(user_id, item_id, quantity=quantity, durability_current=durability_current)


async def create_user_profile(user_id: int):
    """Create a user profile."""
    return await items_service.get_or_create_profile(user_id)


# ---------------------------------------------------------------------------
# Items Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_weapon():
    item_id = await items_service.create_weapon(
        item_id="WPN-TEST-001",
        name="Test Gun",
        rarity="common",
        damage=20,
        accuracy=0.8,
        fire_rate=5,
        ammo=30,
        durability=100,
        price=1000,
        description="A test weapon",
    )
    assert item_id == "WPN-TEST-001"
    
    weapon = await items_service.get_weapon("WPN-TEST-001")
    assert weapon is not None
    assert weapon["name"] == "Test Gun"
    assert weapon["damage"] == 20
    assert weapon["category"] == "weapon"


@pytest.mark.asyncio
async def test_create_armor():
    item_id = await items_service.create_armor(
        item_id="ARM-TEST-001",
        name="Test Armor",
        rarity="rare",
        defense=30,
        base_durability=100,
        price=2000,
    )
    assert item_id == "ARM-TEST-001"
    
    armor = await items_service.get_armor("ARM-TEST-001")
    assert armor is not None
    assert armor["defense"] == 30
    assert armor["category"] == "armor"


@pytest.mark.asyncio
async def test_create_special_weapon():
    item_id = await items_service.create_special_weapon(
        special_id="SP-999",
        name="Test Special",
        description="A test special",
        damage=100,
        effect="test_effect",
    )
    assert item_id == "SP-999"
    
    special = await items_service.get_special_weapon("SP-999")
    assert special is not None
    assert special["damage"] == 100


@pytest.mark.asyncio
async def test_list_weapons():
    await setup_item("WPN-A-001", "weapon", name="Weapon A", damage=10, accuracy=0.7, fire_rate=5, ammo=20, durability=50, price=500)
    await setup_item("WPN-B-001", "weapon", name="Weapon B", damage=30, accuracy=0.85, fire_rate=8, ammo=25, durability=80, price=2000, rarity="rare")
    
    weapons = await items_service.list_weapons(active_only=True)
    assert len(weapons) >= 2
    ids = {w["item_id"] for w in weapons}
    assert "WPN-A-001" in ids
    assert "WPN-B-001" in ids


@pytest.mark.asyncio
async def test_list_armor():
    await setup_item("ARM-A-001", "armor", name="Armor A", defense=10, base_durability=50, price=500)
    await setup_item("ARM-B-001", "armor", name="Armor B", defense=40, base_durability=120, price=3000, rarity="rare")
    
    armor = await items_service.list_armor(active_only=True)
    assert len(armor) >= 2
    ids = {a["item_id"] for a in armor}
    assert "ARM-A-001" in ids
    assert "ARM-B-001" in ids


@pytest.mark.asyncio
async def test_list_special_weapons():
    await setup_item("SP-100", "special", name="Special 100", description="Test", damage=200, effect="test_effect")
    
    specials = await items_service.list_special_weapons(active_only=True)
    assert len(specials) >= 1
    ids = {s["special_id"] for s in specials}
    assert "SP-100" in ids


@pytest.mark.asyncio
async def test_update_weapon():
    await setup_item("WPN-UPDATE-001", "weapon", name="Old Name", damage=10, accuracy=0.7, fire_rate=5, ammo=20, durability=50, price=500)
    
    success = await items_service.update_weapon("WPN-UPDATE-001", name="New Name", damage=25)
    assert success is True
    
    weapon = await items_service.get_weapon("WPN-UPDATE-001")
    assert weapon["name"] == "New Name"
    assert weapon["damage"] == 25


@pytest.mark.asyncio
async def test_delete_weapon():
    await setup_item("WPN-DELETE-001", "weapon", name="To Delete", damage=10, accuracy=0.7, fire_rate=5, ammo=20, durability=50, price=500)
    
    success = await items_service.delete_weapon("WPN-DELETE-001")
    assert success is True
    
    weapon = await items_service.get_weapon("WPN-DELETE-001")
    assert weapon is None


# ---------------------------------------------------------------------------
# Inventory Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_to_inventory():
    await setup_item("WPN-INV-001", "weapon", name="Inv Weapon", damage=10, accuracy=0.7, fire_rate=5, ammo=20, durability=50, price=500)
    
    result = await inventory_service.add_to_inventory(123, "WPN-INV-001", 2)
    assert result["item_id"] == "WPN-INV-001"
    assert result["quantity"] == 2
    assert result["durability_current"] == 50  # default from weapon


@pytest.mark.asyncio
async def test_remove_from_inventory():
    await items_service.create_weapon(
        item_id="WPN-INV-002", name="Removable", rarity="common", damage=10, accuracy=0.7, fire_rate=5, ammo=20, durability=50, price=500
    )
    await inventory_service.add_to_inventory(123, "WPN-INV-002", 5)
    
    success = await inventory_service.remove_from_inventory(123, "WPN-INV-002", 2)
    assert success is True
    
    # Test removing more than available
    success = await inventory_service.remove_from_inventory(123, "WPN-INV-002", 10)
    assert success is False


@pytest.mark.asyncio
async def test_get_user_inventory():
    await setup_item("WPN-INV-003", "weapon", name="Sword", damage=15, accuracy=0.8, fire_rate=3, ammo=1, durability=100, price=1000)
    await inventory_service.add_to_inventory(123, "WPN-INV-003", 1)
    
    inventory = await inventory_service.get_user_inventory(123)
    assert len(inventory) >= 1
    item = next((i for i in inventory if i["item_id"] == "WPN-INV-003"), None)
    assert item is not None
    assert item["category"] == "weapon"


@pytest.mark.asyncio
async def test_get_inventory_by_category():
    await setup_item("ARM-INV-001", "armor", name="Shield", defense=20, base_durability=80, price=1500)
    await setup_item("WPN-INV-004", "weapon", name="Axe", damage=25, accuracy=0.8, fire_rate=3, ammo=1, durability=120, price=2000)
    await inventory_service.add_to_inventory(123, "WPN-INV-004", 1)
    await inventory_service.add_to_inventory(123, "ARM-INV-001", 1)
    
    weapons = await inventory_service.get_inventory_by_category(123, "weapon")
    armor = await inventory_service.get_inventory_by_category(123, "armor")
    
    assert len(weapons) >= 1
    assert len(armor) >= 1


# ---------------------------------------------------------------------------
# Equipment Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_equip_weapon():
    await setup_item("WPN-EQUIP-001", "weapon", name="Equip Sword", damage=50, accuracy=0.9, fire_rate=2, ammo=1, durability=200, price=5000, rarity="rare")
    await inventory_service.add_to_inventory(123, "WPN-EQUIP-001", 1)
    
    result = await inventory_service.equip_weapon(123, "WPN-EQUIP-001")
    assert result["weapon_id"] == "WPN-EQUIP-001"
    
    equipped = await inventory_service.get_equipped(123)
    assert equipped["weapon"]["item_id"] == "WPN-EQUIP-001"
    assert equipped["weapon"]["name"] == "Equip Sword"


@pytest.mark.asyncio
async def test_equip_armor():
    await setup_item("ARM-EQUIP-001", "armor", name="Test Armor", defense=100, base_durability=200, price=10000, rarity="epic")
    await inventory_service.add_to_inventory(123, "ARM-EQUIP-001", 1)
    
    result = await inventory_service.equip_armor(123, "ARM-EQUIP-001")
    assert result["armor_id"] == "ARM-EQUIP-001"
    
    equipped = await inventory_service.get_equipped(123)
    assert equipped["armor"]["item_id"] == "ARM-EQUIP-001"


@pytest.mark.asyncio
async def test_equip_special():
    await setup_item("SP-EQUIP-001", "special", name="Mega Sword", description="Epic", damage=500, effect="mega_slash")
    await inventory_service.add_to_inventory(123, "SP-EQUIP-001", 1)
    
    result = await inventory_service.equip_special(123, "SP-EQUIP-001")
    assert result["special_id"] == "SP-EQUIP-001"
    
    equipped = await inventory_service.get_equipped(123)
    assert equipped["special"]["special_id"] == "SP-EQUIP-001"


@pytest.mark.asyncio
async def test_get_equipped():
    await setup_item("WPN-GET-001", "weapon", name="Get Weapon", damage=10, accuracy=0.7, fire_rate=5, ammo=20, durability=50, price=500)
    await inventory_service.add_to_inventory(123, "WPN-GET-001", 1)
    await inventory_service.equip_weapon(123, "WPN-GET-001")
    
    equipped = await inventory_service.get_equipped(123)
    assert equipped["weapon"]["item_id"] == "WPN-GET-001"
    assert equipped["weapon"]["damage"] == 10


@pytest.mark.asyncio
async def test_equip_weapon_not_owned():
    await setup_item("WPN-NOT-OWNED", "weapon", name="Not Owned", damage=10, accuracy=0.7, fire_rate=5, ammo=20, durability=50, price=500)
    
    import pytest
    with pytest.raises(ValueError, match="You don't own this weapon"):
        await inventory_service.equip_weapon(123, "WPN-NOT-OWNED")


@pytest.mark.asyncio
async def test_equip_none_unequip():
    await inventory_service.equip_weapon(123, "WPN-GET-001")
    result = await inventory_service.equip_weapon(123, None)
    assert result["weapon_id"] is None


# ---------------------------------------------------------------------------
# Durability Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_repair_item():
    await setup_item("WPN-REPAIR-001", "weapon", name="Repair Sword", damage=10, accuracy=0.7, fire_rate=5, ammo=20, durability=100, price=500)
    await inventory_service.add_to_inventory(123, "WPN-REPAIR-001", 1, durability_current=10)
    
    result = await inventory_service.repair_item(123, "WPN-REPAIR-001")
    assert result["durability_restored"] == 100
    
    # Verify durability restored
    inv_item = await inventory_service.get_user_inventory(123)
    item = next(i for i in inv_item if i["item_id"] == "WPN-REPAIR-001")
    assert item["durability_current"] == 100


@pytest.mark.asyncio
async def test_damage_item():
    await setup_item("WPN-DAMAGE-001", "weapon", name="Damage Sword", damage=10, accuracy=0.7, fire_rate=5, ammo=20, durability=100, price=500)
    await inventory_service.add_to_inventory(123, "WPN-DAMAGE-001", 1, durability_current=100)
    
    result = await inventory_service.damage_item(123, "WPN-DAMAGE-001", 30)
    assert result["durability_current"] == 70
    assert result["broken"] is False
    
    # Damage to zero
    result = await inventory_service.damage_item(123, "WPN-DAMAGE-001", 100)
    assert result["durability_current"] == 0
    assert result["broken"] is True


# ---------------------------------------------------------------------------
# Store Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_store_items():
    await setup_item("WPN-STORE-001", "weapon", name="Store Gun", damage=20, accuracy=0.8, fire_rate=5, ammo=30, durability=100, price=2000)
    await setup_item("ARM-STORE-001", "armor", name="Store Armor", defense=30, base_durability=100, price=3000)
    # Special weapon should not appear in store
    await setup_item("SP-STORE-001", "special", name="Special", description="Test", damage=500, effect="test")
    
    store_items = await store_service.get_store_items(active_only=True)
    ids = {item["item_id"] for item in store_items}
    assert "WPN-STORE-001" in ids
    assert "ARM-STORE-001" in ids
    assert "SP-STORE-001" not in ids  # Special weapons not in store


@pytest.mark.asyncio
async def test_get_store_by_category():
    await setup_item("WPN-CAT-001", "weapon", name="Cat Weapon", damage=15, accuracy=0.75, fire_rate=4, ammo=25, durability=80, price=1500)
    
    weapons = await store_service.get_store_by_category("weapon")
    assert any(i["item_id"] == "WPN-CAT-001" for i in weapons)
    
    armor = await store_service.get_store_by_category("armor")
    assert all(i["category"] == "armor" for i in armor)


@pytest.mark.asyncio
async def test_purchase_item(monkeypatch):
    # Mock economy to avoid fake DB issues
    async def fake_remove_gb_coins(user_id, amount, *, spend=True, from_transaction=None):
        return {"wallet": 5000 - amount, "bank": 0}
    
    async def fake_add_gb_coins(user_id, amount, *, earn=True, from_transaction=None):
        return {"wallet": 5000, "bank": 0}
    
    import services.global_battle.currency as currency_service
    import services.transaction as tx_service
    
    # Mock the services
    monkeypatch.setattr("services.global_battle.currency.remove_gb_coins", fake_remove_gb_coins)
    monkeypatch.setattr("services.global_battle.currency.add_gb_coins", fake_add_gb_coins)
    
    async def _mock_get_gb_balance(uid):
        return 5000
    
    monkeypatch.setattr("services.global_battle.currency.get_gb_balance", _mock_get_gb_balance)
    
    recorded = []
    async def fake_record(**kwargs):
        recorded.append(kwargs)
        return "tx-1"
    import services.transaction as tx_service
    monkeypatch.setattr(tx_service, "record", fake_record)
    
    # Setup item
    await setup_item("WPN-PURCHASE-001", "weapon", name="Buyable Gun", damage=25, accuracy=0.85, fire_rate=6, ammo=40, durability=120, price=1000)
    
    result = await store_service.purchase_item(123, "WPN-PURCHASE-001", 2)
    assert result["item_id"] == "WPN-PURCHASE-001"
    assert result["quantity"] == 2
    assert result["total_price"] == 2000
    assert result["new_gb_balance"] == 3000  # 5000 - 2000
    
    # Check transaction recorded
    assert len([r for r in recorded if r.get("ttype") == "GB_PURCHASE"]) >= 1


@pytest.mark.asyncio
async def test_purchase_item_insufficient_gb():
    import pytest
    
    async def test_impl():
        await setup_item("WPN-EXPENSIVE-001", "weapon", name="Expensive Gun", damage=100, accuracy=0.95, fire_rate=10, ammo=50, durability=500, price=100000)
        
        # Mock get_gb_balance to return 0
        import services.global_battle.currency as currency_service
        from services.economy import InsufficientBalance
        
        async def fake_get_balance(uid):
            return 0
        import services.global_battle.currency as currency_module
        original_get_balance = currency_module.get_gb_balance
        currency_module.get_gb_balance = fake_get_balance
        
        try:
            await store_service.purchase_item(123, "WPN-EXPENSIVE-001", 1)
            assert False, "Should have raised InsufficientBalance"
        except Exception as exc:
            assert "InsufficientBalance" in str(type(exc))
        finally:
            currency_module.get_gb_balance = original_get_balance
    
    await test_impl()


@pytest.mark.asyncio
async def test_purchase_special_weapon_rejected():
    import pytest
    
    await setup_item("SP-EXPENSIVE-001", "special", name="Legendary Sword", description="Legendary", damage=1000, effect="legendary")
    
    try:
        await store_service.purchase_item(123, "SP-EXPENSIVE-001", 1)
        assert False, "Should have raised ValueError"
    except ValueError as exc:
        assert "Special weapons cannot be purchased" in str(exc)


@pytest.mark.asyncio
async def test_get_store_preview():
    await setup_item("WPN-PREVIEW-001", "weapon", name="Preview Gun", damage=15, accuracy=0.8, fire_rate=5, ammo=25, durability=80, price=1200)
    
    preview = await store_service.get_store_preview(123)
    assert "gb_balance" in preview
    assert "weapons" in preview
    assert "armor" in preview
    assert "utility" in preview
    assert any(w["item_id"] == "WPN-PREVIEW-001" for w in preview["weapons"])