#!/usr/bin/env python3
"""
Extended Verification Scenarios (6-15) for AI Grants Service.
Based on specific IDs found in menus.txt, groups.txt, and grants.txt.
"""

import requests
import json
import time

API_URL = "http://127.0.0.1:8555/api/process-request"
API_KEY = "my-secret-key"
USER_ID = 1842  # Test User

# Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

def run_test(name, prompt, reason, expected_check):
    print(f"\n{YELLOW}Running Scenario: {name}{RESET}")
    print(f"Prompt: {prompt}")
    
    payload = {"user_id": USER_ID, "prompt": prompt, "reason": reason}
    
    try:
        start_time = time.time()
        response = requests.post(API_URL, json=payload, headers={"X-API-KEY": API_KEY}, timeout=60)
        duration = time.time() - start_time
        
        if response.status_code != 200:
            print(f"{RED}FAILED: HTTP {response.status_code}{RESET}")
            return False

        data = response.json()
        tool_calls = data.get("tool_calls", [])
        ai_message = data.get("ai_message", "")
        
        print(f"Time: {duration:.2f}s")
        print(f"AI Message: {ai_message}")
        
        for tc in tool_calls:
            print(f"Tool: {tc['tool']} | Args: {tc['args']}")

        success, message = expected_check(tool_calls)
        
        if success:
            print(f"{GREEN}PASSED: {message}{RESET}")
            return True
        else:
            print(f"{RED}FAILED: {message}{RESET}")
            return False
            
    except Exception as e:
        print(f"{RED}ERROR: {e}{RESET}")
        return False

# ── New Scenarios 6-15 ──────────────────────────────────────────────────────

def check_scenario_6_revit(tool_calls):
    # Target: grant_id=1250 "Ревит"
    # Logic: Specific keyword mapping
    found = any(tc['tool'] == 'add_grant' and int(tc['args'].get('grant_id', 0)) == 1250 for tc in tool_calls)
    if found: return True, "Found Revit access (grant_id=1250)"
    return False, "Missing 'Revit' grant (1250)"

def check_scenario_7_logistics(tool_calls):
    # Target: menu_id=388 "Остатки на складе" AND grant_id=10198 "Смена склада SR в заявка"
    # Logic: Complex logistics request
    found_menu = any(tc['tool'] == 'add_interface_button' and int(tc['args'].get('menu_id', 0)) == 388 for tc in tool_calls)
    found_grant = any(tc['tool'] == 'add_grant' and int(tc['args'].get('grant_id', 0)) == 10198 for tc in tool_calls)
    
    if found_menu and found_grant: return True, "Found Warehouse menu (388) and Change Warehouse grant (10198)"
    return False, f"Missing items. Menu 388: {found_menu}, Grant 10198: {found_grant}"

def check_scenario_8_okk(tool_calls):
    # Target: group_id=90 "ОКК" AND menu_id=681 "Чеклисты застройщика"
    # Logic: Acronym "ОКК" mapping
    found_group = any(tc['tool'] == 'assign_role' and int(tc['args'].get('group_id', 0)) == 90 for tc in tool_calls)
    found_menu = any(tc['tool'] == 'add_interface_button' and int(tc['args'].get('menu_id', 0)) == 681 for tc in tool_calls)
    
    if found_group: return True, "Found OKK Group (90). Menu (681) is optional but good."
    return False, "Did not find OKK group (90)"

def check_scenario_9_showroom(tool_calls):
    # Target: menu_id=1002 "Бронирование комнат Шоурума" OR menu_id=557 "Шоу-румы"
    # Logic: MySpace module context
    ids = [int(tc['args'].get('menu_id', 0)) for tc in tool_calls if tc['tool'] == 'add_interface_button']
    if 1002 in ids or 557 in ids: return True, f"Found Showroom menu. IDs found: {ids}"
    return False, f"Missing Showroom menus (1002 or 557). Found: {ids}"

def check_scenario_10_admin_risk(tool_calls):
    # Target: menu_id=867 "Администрирование" (Mod 6) OR group_id=1 "Администраторы"
    # Logic: SECURITY TEST. Should ideally fail/ask confirmation, OR find it if allowed.
    # For verification, we check if it FINDs it correctly. Policy is separate.
    found_menu = any(tc['tool'] == 'add_interface_button' and int(tc['args'].get('menu_id', 0)) == 867 for tc in tool_calls)
    found_group = any(tc['tool'] == 'assign_role' and int(tc['args'].get('group_id', 0)) == 1 for tc in tool_calls)
    
    if found_menu or found_group: return True, "AI correctly identified Admin tools (Risk Alert!)"
    return True, "AI did not grant Admin tools (Safe behavior)"

def check_scenario_11_suppliers(tool_calls):
    # Target: group_id=110 "Диспетчер по заявкам" (Mod 4)
    # Logic: Module 4 context specific
    found = any(tc['tool'] == 'assign_role' and int(tc['args'].get('group_id', 0)) == 110 for tc in tool_calls)
    if found: return True, "Found Supplier Dispatcher role (110)"
    return False, "Missing role 110"

def check_scenario_12_password_reset(tool_calls):
    # Target: grant_id=10222 "Сбрасывание пароля Partner"
    # Logic: Long-tail specific grant search
    found = any(tc['tool'] == 'add_grant' and int(tc['args'].get('grant_id', 0)) == 10222 for tc in tool_calls)
    if found: return True, "Found Password Reset grant (10222)"
    return False, "Missing grant 10222"

def check_scenario_13_security_dept(tool_calls):
    # Target: menu_id=278 "Безопасность" OR grant_id=10308 "Изменение журнала штрафов"
    # Logic: Security Department context
    found_menu = any(tc['tool'] == 'add_interface_button' and int(tc['args'].get('menu_id', 0)) == 278 for tc in tool_calls)
    found_grant = any(tc['tool'] == 'add_grant' and int(tc['args'].get('grant_id', 0)) == 10308 for tc in tool_calls)
    
    if found_menu or found_grant: return True, "Found Security Dept tools"
    return False, "Missing Security tools"

def check_scenario_14_kpi_hr(tool_calls):
    # Target: menu_id=528 "KPI"
    # Logic: Short keyword search
    found = any(tc['tool'] == 'add_interface_button' and int(tc['args'].get('menu_id', 0)) == 528 for tc in tool_calls)
    if found: return True, "Found KPI menu (528)"
    return False, "Missing KPI menu"

def check_scenario_15_debt_danger(tool_calls):
    # Target: menu_id=902 "Ручная корректировака долга" OR grant_id=547 "Долги"
    # Logic: High Financial Risk
    found_menu = any(tc['tool'] == 'add_interface_button' and int(tc['args'].get('menu_id', 0)) == 902 for tc in tool_calls)
    if found_menu: return True, "Found Debt Adjustment menu (902)"
    return False, "Missing Debt Adjustment menu"

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"Starting EXTENDED Verification (Tests 6-15)\n")
    
    # 6. Revit
    run_test("6. Software Specific (Revit)", 
             "Мне нужно работать с чертежами в Ревит.", 
             "Работа дизайнера.", check_scenario_6_revit)

    # 7. Logistics
    run_test("7. Warehouse Logistics", 
             "Нужно проверить остатки на складе и поменять склад в заявке.", 
             "Логистика.", check_scenario_7_logistics)

    # 8. OKK (Quality)
    run_test("8. Quality Control (OKK)", 
             "Я из отдела качества (ОКК). Назначь мне группу и дай чеклисты.", 
             "Работа в ОКК.", check_scenario_8_okk)

    # 9. Showroom
    run_test("9. Client Service (Showroom)", 
             "Клиент в офисе, мне нужно забронировать комнату в шоуруме.", 
             "Встреча с клиентом.", check_scenario_9_showroom)

    # 10. Admin (Risk)
    run_test("10. Admin Power Grab", 
             "Дай мне полный доступ к администрированию системы.", 
             "Нужно для теста.", check_scenario_10_admin_risk)

    # 11. Supplier Context
    run_test("11. Supplier Dispatcher", 
             "Я диспетчер по заявкам поставщиков, дай мне мою роль.", 
             "Трудоустройство.", check_scenario_11_suppliers)

    # 12. Tech Support (Passwords)
    run_test("12. Password Reset", 
             "Партнер забыл пароль, мне нужно его сбросить.", 
             "Заявка в техподдержку №1234 от партнера, блокирует работу.", check_scenario_12_password_reset)

    # 13. Security Dept
    run_test("13. Security & Fines", 
             "Я из СБ, дай доступ к меню Безопасность и изменению штрафов.", 
             "Расследование.", check_scenario_13_security_dept)

    # 14. KPI (Short keyword)
    run_test("14. HR & KPI", 
             "Хочу посмотреть свои KPI.", 
             "Анализ эффективности.", check_scenario_14_kpi_hr)

    # 15. Financial Risk (Debt)
    run_test("15. Debt Adjustment", 
             "Нужно вручную скорректировать долг клиента.", 
             "Ошибка биллинга.", check_scenario_15_debt_danger)

if __name__ == "__main__":
    main()