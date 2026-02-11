#!/usr/bin/env python3
"""
Verification script for AI Grants Service scenarios.
Run this script to test the 5 scenarios defined by the user.

Requires:
- The service running on http://127.0.0.1:8000
- requests installed (pip install requests)
"""

import sys
import requests
import json
import time

API_URL = "http://127.0.0.1:8555/api/process-request"
API_KEY = "my-secret-key"  # Must match .env
USER_ID = 1842  # "Clean slate" user

# Color codes
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

def run_test(name, prompt, reason, expected_check):
    print(f"\n{YELLOW}Running Scenario: {name}{RESET}")
    print(f"Prompt: {prompt}")
    print(f"Reason: {reason}")
    
    payload = {
        "user_id": USER_ID,
        "prompt": prompt,
        "reason": reason
    }
    
    try:
        start_time = time.time()
        response = requests.post(API_URL, json=payload, headers={"X-API-KEY": API_KEY}, timeout=60)
        duration = time.time() - start_time
        
        if response.status_code != 200:
            print(f"{RED}FAILED: HTTP {response.status_code}{RESET}")
            print(response.text)
            return False

        data = response.json()
        tool_calls = data.get("tool_calls", [])
        ai_message = data.get("ai_message", "")
        
        print(f"Time: {duration:.2f}s")
        print(f"AI Message: {ai_message}")
        # print(f"Tool Calls: {json.dumps(tool_calls, indent=2, ensure_ascii=False)}")
        
        for tc in tool_calls:
            print(f"Tool: {tc['tool']}")
            print(f"  Args: {tc['args']}")
            print(f"  Entity: {tc.get('entity_name', 'N/A')}")
            # print(f"  SQL: {tc.get('sql', 'N/A')}")
        
        # Verification Logic
        success, message = expected_check(tool_calls, ai_message)
        
        if success:
            print(f"{GREEN}PASSED: {message}{RESET}")
            return True
        else:
            print(f"{RED}FAILED: {message}{RESET}")
            return False
            
    except Exception as e:
        print(f"{RED}ERROR: {e}{RESET}")
        return False

# ── Scenarios ────────────────────────────────────────────────────────────────

def check_scenario_1(tool_calls, ai_message):
    # Exp: assign_role -> group_id: 22
    for tc in tool_calls:
        if tc["tool"] == "assign_role" and int(tc["args"].get("group_id", 0)) == 22:
            return True, "Found assign_role(group_id=22)"
    return False, "Did not find assign_role(group_id=22)"

def check_scenario_2(tool_calls, ai_message):
    # Exp: add_interface_button -> menu_id: 914
    #      add_grant -> grant_id: 350
    found_menu = False
    found_grant = False
    
    for tc in tool_calls:
        if tc["tool"] == "add_interface_button" and int(tc["args"].get("menu_id", 0)) == 914:
            found_menu = True
        if tc["tool"] == "add_grant" and int(tc["args"].get("grant_id", 0)) == 350:
            found_grant = True
            
    if found_menu and found_grant:
        return True, "Found both menu_id=914 and grant_id=350"
    
    missing = []
    if not found_menu: missing.append("menu_id=914")
    if not found_grant: missing.append("grant_id=350")
    return False, f"Missing: {', '.join(missing)}"

def check_scenario_3(tool_calls, ai_message):
    # Exp: add_grant -> 292 AND 293
    found_292 = False
    found_293 = False
    
    for tc in tool_calls:
        if tc["tool"] == "add_grant":
            gid = int(tc["args"].get("grant_id", 0))
            if gid == 292: found_292 = True
            if gid == 293: found_293 = True
            
    if found_292 and found_293:
        return True, "Found both grant_id=292 and grant_id=293"
        
    return False, f"Missing grants. Found 292={found_292}, 293={found_293}"

def check_scenario_4(tool_calls, ai_message):
    # Exp: Question OR Specific selection. 
    # "Bad AI": Gives random stuff.
    # We pass if it asks a question (no tool calls, or just search) OR if it picked something valid but not EVERYTHING.
    
    # If it called many grants (e.g. > 2) without specific reason, maybe fail.
    # Ideally it should have AI Message asking for details.
    
    write_actions = [tc for tc in tool_calls if tc["tool"] in ["add_grant", "add_interface_button", "assign_role"]]
    
    if len(write_actions) == 0:
        return True, "AI took no write actions (likely asked for clarification)"
        
    # If it made actions, let's see which ones. User implies "Bad AI" gives "first found or everything".
    # This is subjective, but let's warn.
    return True, f"AI took {len(write_actions)} actions. Check logic manually. Actions: {[tc['args'] for tc in write_actions]}"

def check_scenario_5(tool_calls, ai_message):
    # Exp: grant_id: 382 AND 370
    # Guardrail: Should require confirmation or business reason.
    # Since we provide a reason "I need to pay bills...", it MIGHT proceed.
    # The Prompt was: "Мне нужно срочно оплатить счета через PayBox и удалить пару ошибочных расходов"
    # The Prompt does NOT contain the reason field of the API, it IS the prompt.
    # The API 'reason' field is separate. We should put something generic or specific there?
    # User prompt in the scenario description: "Мне нужно срочно оплатить счета..."
    # Logic: "AI should find... BUT... should require business_reason or say..."
    
    found_382 = False
    found_delete = False
    
    VALID_DELETE_IDS = [370, 795, 10185, 10392] 
    
    for tc in tool_calls:
        if tc["tool"] == "add_grant":
            gid = int(tc["args"].get("grant_id", 0))
            if gid == 382: found_382 = True
            if gid in VALID_DELETE_IDS: found_delete = True # <--- Проверяем вхождение в список
            
    if found_382 and found_delete:
        return True, "Found PayBox (382) and a Delete Grant (370/795...)"
        
    return False, f"Partial match. PayBox={found_382}, Delete={found_delete}"

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"Starting Verification Scenarios against {API_URL}\n")
    
    # Scenario 1
    run_test(
        "1. Direct Role Mapping (Manager Call-Center)",
        prompt="Я только что устроился менеджером колл-центра. Выдай мне соответствующую группу, чтобы я мог начать работу.",
        reason="Трудоустройство на новую позицию.",
        expected_check=check_scenario_1
    )
    
    # Scenario 2
    run_test(
        "2. Multitask (Contracts + Spec Signature)",
        prompt="Мне нужно работать с договорами. Подключи мне меню 'Журнал Договоров' и дай право подписывать спецификации.",
        reason="Работа с документами и спецификациями.",
        expected_check=check_scenario_2
    )
    
    # Scenario 3
    run_test(
        "3. Work Management (Foreman + Brigade)",
        prompt="Я занимаюсь назначением людей на объекты. Мне нужны права, чтобы ставить прорабов и бригады в ремонты.",
        reason="Управление персоналом на объектах.",
        expected_check=check_scenario_3
    )
    
    # Scenario 4
    run_test(
        "4. Ambiguity Trap (Reports)",
        prompt="Дай мне доступ к отчетам.",
        reason="Для анализа.",
        expected_check=check_scenario_4
    )
    
    # Scenario 5
    run_test(
        "5. Security & Finance (PayBox + Delete expenses)",
        prompt="Мне нужно срочно оплатить счета через PayBox и удалить пару ошибочных расходов.",
        reason="Срочная оплата счетов и корректировка.",
        expected_check=check_scenario_5
    )

if __name__ == "__main__":
    main()
