#!/usr/bin/env python3
import os
import argparse
import multiprocessing
from multiprocessing import Pool, Manager
from typing import Tuple, Dict, List
import re
from datetime import datetime
from difflib import SequenceMatcher
import sys
import gc
import tqdm
import logging
import csv
#pyinstaller --onefile --optimize=2 --strip --name=text_comparison_linux_007 text_comparison_linux_007.py
def check_double(is_double_text: str, mode: str = "ds") -> str:
    if mode == "ds":
        return re.sub(r'\([^)]*\)|\([^(]*\/[^)]*\)', '', is_double_text)
    elif mode == "num":
        matches = re.findall(r'\(([^)]*)\)\/|\(\/([^)]*)\)', is_double_text)
        filtered = re.sub(r'\([^)]*\)\/|\(\/[^)]*\)', '', is_double_text)
        for match in matches:
            filtered += match[1] if match[1] else match[0]
        return filtered
    elif mode == "phone":
        matches = re.findall(r'\(([^)]*)\)\/|\(\/([^)]*)\)', is_double_text)
        filtered = re.sub(r'\([^)]*\)\/|\(\/[^)]*\)', '', is_double_text)
        for match in matches:
            filtered += match[0] if match[0] else match[1]
        return filtered
    return is_double_text


def levenshtein(u: str, v: str) -> Tuple[int, Tuple[int, int, int]]:
    if not u:
        return len(v), (0, 0, len(v))
    if not v:
        return len(u), (0, len(u), 0)
    
    if len(u) < len(v):
        u, v = v, u
        swap = True
    else:
        swap = False
    
    prev = None
    curr = [0] + list(range(1, len(v) + 1))
    curr_ops = [(0, 0, i) for i in range(len(v) + 1)]
    
    for x in range(1, len(u) + 1):
        prev, curr = curr, [x] + ([0] * len(v))
        prev_ops, curr_ops = curr_ops, [(0, x, 0)] + [(0, 0, 0)] * len(v)
        
        for y in range(1, len(v) + 1):
            delcost = prev[y] + 1
            addcost = curr[y - 1] + 1
            subcost = prev[y - 1] + int(u[x - 1] != v[y - 1])
            
            if subcost <= delcost and subcost <= addcost:
                curr[y] = subcost
                (n_s, n_d, n_i) = prev_ops[y - 1]
                curr_ops[y] = (n_s + int(u[x - 1] != v[y - 1]), n_d, n_i)
            elif delcost <= addcost:
                curr[y] = delcost
                (n_s, n_d, n_i) = prev_ops[y]
                curr_ops[y] = (n_s, n_d + 1, n_i)
            else:
                curr[y] = addcost
                (n_s, n_d, n_i) = curr_ops[y - 1]
                curr_ops[y] = (n_s, n_d, n_i + 1)
    
    if swap:
        (s, d, i) = curr_ops[len(v)]
        return curr[len(v)], (s, i, d)
    return curr[len(v)], curr_ops[len(v)]


def measure_cer(reference: str, transcription: str) -> Tuple[int, int, int, int, list, list, list]:
    """
    문자 오류율(CER)을 계산하고 변경된 문자들의 상세 정보를 반환합니다.
    """
    substituted_chars = []  # (원본, 변경) 튜플 리스트
    deleted_chars = []      # 삭제된 문자 리스트
    inserted_chars = []     # 삽입된 문자 리스트
    
    num_substitutions = 0
    num_deletions = 0
    num_insertions = 0
    
    # 레벤슈타인 거리 계산을 위한 행렬 초기화
    m, n = len(transcription), len(reference)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    
    # 첫 행과 열 초기화
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    
    # 행렬 채우기
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if transcription[i-1] == reference[j-1]:
                dp[i][j] = dp[i-1][j-1]
            else:
                dp[i][j] = min(dp[i-1][j-1], dp[i-1][j], dp[i][j-1]) + 1
    
    # 역추적하여 변경 사항 수집
    i, j = m, n
    while i > 0 and j > 0:
        if transcription[i-1] == reference[j-1]:
            i, j = i-1, j-1
        else:
            if dp[i][j] == dp[i-1][j-1] + 1:  # 대체
                substituted_chars.append((reference[j-1], transcription[i-1]))
                num_substitutions += 1
                i, j = i-1, j-1
            elif dp[i][j] == dp[i-1][j] + 1:  # 삽입
                inserted_chars.append(transcription[i-1])
                num_insertions += 1
                i -= 1
            else:  # 삭제
                deleted_chars.append(reference[j-1])
                num_deletions += 1
                j -= 1
    
    # 남은 문자 처리
    while i > 0:
        inserted_chars.append(transcription[i-1])
        num_insertions += 1
        i -= 1
    while j > 0:
        deleted_chars.append(reference[j-1])
        num_deletions += 1
        j -= 1
    
    # 리스트 역순으로 변경 (원래 순서대로 표시)
    substituted_chars.reverse()
    deleted_chars.reverse()
    inserted_chars.reverse()
    
    # 기존 통계 계산
    _, (s, i, d) = levenshtein(transcription, reference)
    hits = len(reference) - (s + d)
    
    return hits, num_substitutions, num_deletions, num_insertions, substituted_chars, deleted_chars, inserted_chars


def extract_timestamp_segments(text: str) -> list:
    """
    텍스트에서 타임스탬프 세그먼트를 추출합니다.
    """
    segments = []
    timestamp_pattern = r'start:\s*([^\,]*),\s*end:\s*([^\,]*),\s*spk\s*(\d*)\s*:\s*([^\n]*)'
    matches = re.findall(timestamp_pattern, text)
    
    for match in matches:
        start_time, end_time, speaker, content = match
        segments.append({
            'start': float(start_time.strip()) if start_time.strip() else 0.0,
            'end': float(end_time.strip()) if end_time.strip() else 0.0,
            'speaker': int(speaker) if speaker.strip() else 0,
            'text': content.strip()
        })
    
    return segments


def create_char_timestamp_mapping(text_segments, processed_text, rm_punctuation=True, ignore_case=False, eli_gantu=False):
    """
    전처리된 텍스트의 각 글자에 대해 타임스탬프 정보를 매핑합니다.
    
    Args:
        text_segments: extract_timestamp_segments로 추출된 세그먼트 리스트
        processed_text: preprocess_text로 전처리된 텍스트
        rm_punctuation: 구두점 제거 여부
        ignore_case: 대소문자 무시 여부
        eli_gantu: 감탄사 제거 여부
    
    Returns:
        dict: {글자_인덱스: {'start': 시작시간, 'end': 끝시간, 'speaker': 화자번호}}
    """
    char_timestamp_map = {}
    current_char_index = 0
    
    for segment in text_segments:
        # 세그먼트 텍스트를 동일한 방식으로 전처리
        segment_processed = preprocess_text(segment['text'], rm_punctuation, ignore_case, preserve_timestamps=False, eli_gantu=eli_gantu)
        
        # 현재 세그먼트의 각 글자에 타임스탬프 정보 매핑
        for char in segment_processed:
            if current_char_index < len(processed_text):
                char_timestamp_map[current_char_index] = {
                    'start': segment['start'],
                    'end': segment['end'], 
                    'speaker': segment['speaker'],
                    'segment_text': segment['text']
                }
                current_char_index += 1
    
    return char_timestamp_map


def remove_eli_gantu_words(text: str) -> str:
    """
    단독으로 나타나는 '이', '그', '저', '뭐', '어' 단어들을 제거합니다.
    """
    # 단독으로 나타나는 감탄사 패턴 제거
    eli_gantu_pattern = r'(?<!\S)(이|그|저|뭐|어)(?!\S)'
    text = re.sub(eli_gantu_pattern, '', text)
    return text


def preprocess_text(text: str, remove_punctuation: bool = True, ignore_case: bool = False, preserve_timestamps: bool = False, eli_gantu: bool = False) -> str:
    """
    텍스트 전처리를 수행합니다.
    """
    if not preserve_timestamps:
        # 타임스탬프 패턴 제거 (start: 시간, end: 시간, spk 번호 : 형태)
        # 예: "start: 0.0, end: 8.1, spk 0 : " 패턴을 제거
        timestamp_pattern = r'start:\s*[^\,]*,\s*end:\s*[^\,]*,\s*spk\s*\d*\s*:\s*'
        text = re.sub(timestamp_pattern, '', text)
    
    # 개행 제거 (줄바꿈을 공백으로 변환)
    text = text.replace('\n', ' ')
    
    # eli_gantu 옵션이 True인 경우 감탄사 제거
    if eli_gantu:
        text = remove_eli_gantu_words(text)
    
    # 괄호 안의 띄어쓰기 제거
    text = re.sub(r'\(([^)]*)\)', lambda m: '(' + m.group(1).replace(' ', '') + ')', text)
    
    if remove_punctuation:
        # 구두점 제거 (한글/영어 구두점 모두 제거)
        pattern = r'[\s\.,\?!:;\-\'\"\(\)\[\]\{\}，．。、？！：；－\'\"\(\)\[\]\{\}]'
        text = re.sub(pattern, '', text)
    else:
        # 띄어쓰기만 제거
        text = text.replace(" ", "")
    
    # 대소문자 무시 옵션
    if ignore_case:
        text = text.lower()
    
    return text


def get_visual_comparison_text(text1, text2, text2_char_timestamps=None):
    """시각적 비교 결과 텍스트를 반환합니다."""
    # CER 계산과 동일한 방식으로 변경 사항 추적
    m, n = len(text2), len(text1)  # text2: 인식 텍스트, text1: 참조 텍스트
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    
    # 첫 행과 열 초기화
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    
    # 행렬 채우기
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if text2[i-1] == text1[j-1]:
                dp[i][j] = dp[i-1][j-1]
            else:
                dp[i][j] = min(dp[i-1][j-1], dp[i-1][j], dp[i][j-1]) + 1
    
    # 역추적하여 변경 사항 표시
    i, j = m, n
    operations = []  # 연산 기록
    
    while i > 0 and j > 0:
        if text2[i-1] == text1[j-1]:
            operations.append(('same', text2[i-1]))
            i, j = i-1, j-1
        else:
            if dp[i][j] == dp[i-1][j-1] + 1:  # 대체
                # 대체된 문자가 공백인 경우 특별 처리
                if text2[i-1].isspace() or text1[j-1].isspace():
                    if text2[i-1].isspace():
                        operations.append(('insert', text2[i-1]))
                        i -= 1
                    else:
                        operations.append(('delete', text1[j-1]))
                        j -= 1
                else:
                    operations.append(('substitute', text2[i-1], text1[j-1]))
                    i, j = i-1, j-1
            elif dp[i][j] == dp[i-1][j] + 1:  # 삽입
                operations.append(('insert', text2[i-1]))
                i -= 1
            else:  # 삭제
                operations.append(('delete', text1[j-1]))
                j -= 1
    
    # 남은 문자 처리
    while i > 0:
        operations.append(('insert', text2[i-1]))
        i -= 1
    while j > 0:
        operations.append(('delete', text1[j-1]))
        j -= 1
    
    # 연산 기록 역순으로 변경
    operations.reverse()
    
    # 연속된 연산 그룹화
    grouped_operations = []
    i = 0
    while i < len(operations):
        # 대체 연산 그룹화
        if operations[i][0] == 'substitute':
            # 연속된 대체 연산 찾기
            j = i + 1
            src = operations[i][1]
            dst = operations[i][2]
            
            while j < len(operations) and operations[j][0] == 'substitute':
                src += operations[j][1]
                dst += operations[j][2]
                j += 1
                
            if j > i + 1:  # 연속된 대체 연산이 있는 경우
                grouped_operations.append(('group_substitute', src, dst))
                i = j
            else:
                grouped_operations.append(operations[i])
                i += 1
        # 삽입 연산 그룹화
        elif operations[i][0] == 'insert':
            # 연속된 삽입 연산 찾기
            j = i + 1
            content = operations[i][1]
            
            while j < len(operations) and operations[j][0] == 'insert':
                content += operations[j][1]
                j += 1
                
            if j > i + 1:  # 연속된 삽입 연산이 있는 경우
                grouped_operations.append(('group_insert', content))
                i = j
            else:
                grouped_operations.append(operations[i])
                i += 1
        # 삭제 연산 그룹화
        elif operations[i][0] == 'delete':
            # 연속된 삭제 연산 찾기
            j = i + 1
            content = operations[i][1]
            
            while j < len(operations) and operations[j][0] == 'delete':
                content += operations[j][1]
                j += 1
                
            if j > i + 1:  # 연속된 삭제 연산이 있는 경우
                grouped_operations.append(('group_delete', content))
                i = j
            else:
                grouped_operations.append(operations[i])
                i += 1
        else:
            grouped_operations.append(operations[i])
            i += 1
    
    # 각 연산별 태그 위치 정보와 결과 문자열 구성을 동시에 생성
    result_text = ""
    diff_tags = []
    delete_tags = []
    insert_tags = []
    char_positions = []  # 각 글자의 원본 위치와 타임스탬프 정보 저장
    
    # text2에서의 현재 위치 추적 (타임스탬프 매핑용)
    text2_pos = 0
    
    for op in grouped_operations:
        current_pos = len(result_text)
        
        if op[0] == 'same':
            # 동일한 글자들 - text2의 타임스탬프 정보 사용
            for char in op[1]:
                char_info = {'char': char, 'type': 'same', 'result_pos': len(result_text)}
                if text2_char_timestamps and text2_pos in text2_char_timestamps:
                    char_info['timestamp'] = text2_char_timestamps[text2_pos]
                char_positions.append(char_info)
                result_text += char
                text2_pos += 1
                
        elif op[0] == 'insert':
            insert_start = len(result_text)
            result_text += "["
            # 삽입된 글자들의 타임스탬프 정보
            for char in op[1]:
                char_info = {'char': char, 'type': 'insert', 'result_pos': len(result_text)}
                if text2_char_timestamps and text2_pos in text2_char_timestamps:
                    char_info['timestamp'] = text2_char_timestamps[text2_pos]
                char_positions.append(char_info)
                result_text += char
                text2_pos += 1
            result_text += "]"
            insert_tags.append((insert_start, len(result_text)))
                
        elif op[0] == 'delete':
            delete_start = len(result_text)
            result_text += "["
            # 삭제된 글자들은 text1에서 온 것이므로 타임스탬프 정보 없음
            for char in op[1]:
                char_info = {'char': char, 'type': 'delete', 'result_pos': len(result_text)}
                char_positions.append(char_info)
                result_text += char
            result_text += "]"
            delete_tags.append((delete_start, len(result_text)))
                
        elif op[0] == 'substitute':
            diff_start = len(result_text)
            result_text += "["
            # 대체된 글자 - text2(인식 텍스트)의 타임스탬프 정보 사용
            char_info = {'char': op[1], 'type': 'substitute', 'result_pos': len(result_text)}
            if text2_char_timestamps and text2_pos in text2_char_timestamps:
                char_info['timestamp'] = text2_char_timestamps[text2_pos]
            char_positions.append(char_info)
            result_text += op[1] + "→" + op[2] + "]"
            diff_tags.append((diff_start, len(result_text)))
            text2_pos += 1
            
        elif op[0] == 'group_substitute':
            diff_start = len(result_text)
            result_text += "["
            # 그룹 대체된 글자들
            for char in op[1]:
                char_info = {'char': char, 'type': 'group_substitute', 'result_pos': len(result_text)}
                if text2_char_timestamps and text2_pos in text2_char_timestamps:
                    char_info['timestamp'] = text2_char_timestamps[text2_pos]
                char_positions.append(char_info)
                text2_pos += 1
            result_text += op[1] + "→" + op[2] + "]"
            diff_tags.append((diff_start, len(result_text)))
                
        elif op[0] == 'group_insert':
            insert_start = len(result_text)
            result_text += "["
            # 그룹 삽입된 글자들
            for char in op[1]:
                char_info = {'char': char, 'type': 'group_insert', 'result_pos': len(result_text)}
                if text2_char_timestamps and text2_pos in text2_char_timestamps:
                    char_info['timestamp'] = text2_char_timestamps[text2_pos]
                char_positions.append(char_info)
                text2_pos += 1
            result_text += op[1] + "]"
            insert_tags.append((insert_start, len(result_text)))
                
        elif op[0] == 'group_delete':
            delete_start = len(result_text)
            result_text += "["
            # 그룹 삭제된 글자들은 text1에서 온 것이므로 타임스탬프 정보 없음
            for char in op[1]:
                char_info = {'char': char, 'type': 'group_delete', 'result_pos': len(result_text)}
                char_positions.append(char_info)
            result_text += op[1] + "]"
            delete_tags.append((delete_start, len(result_text)))
    
    # 빈 괄호 제거
    result_text = re.sub(r'\[\s*\]', '', result_text)
    
    # 태그 정보도 함께 반환
    tag_info = {
        'visual_result': result_text,
        'visual_tags': {
            'diff_tags': diff_tags,
            'delete_tags': delete_tags,
            'insert_tags': insert_tags
        },
        'char_positions': char_positions  # 글자별 위치와 타임스탬프 정보
    }
    
    return tag_info


def get_visual_comparison_with_timestamps(text1, text2, text2_segments=None):
    """타임스탬프 정보를 포함한 시각적 비교 결과를 반환합니다."""
    # 기존 시각적 비교 결과 생성
    basic_result = get_visual_comparison_text(text1, text2)
    
    # 타임스탬프 세그먼트가 있는 경우 추가 정보 포함
    if text2_segments:
        # 세그먼트별로 타임스탬프 정보를 매핑
        result_with_timestamps = ""
        timestamp_info = []
        
        for segment in text2_segments:
            segment_text = segment['text']
            # 전처리된 텍스트와 매칭
            processed_segment = preprocess_text(segment_text, preserve_timestamps=False)
            
            # 현재 위치에서 세그먼트 시작
            start_pos = len(result_with_timestamps)
            result_with_timestamps += f'<span class="timestamp-segment" data-start="{segment["start"]}" data-end="{segment["end"]}" data-speaker="{segment["speaker"]}">{segment_text}</span> '
            end_pos = len(result_with_timestamps)
            
            timestamp_info.append({
                'start_time': segment['start'],
                'end_time': segment['end'],
                'speaker': segment['speaker'],
                'text': segment_text,
                'start_pos': start_pos,
                'end_pos': end_pos
            })
        
        return {
            'visual_result': basic_result['visual_result'],
            'visual_tags': basic_result['visual_tags'],
            'timestamp_result': result_with_timestamps,
            'timestamp_info': timestamp_info
        }
    
    return basic_result


def get_cers(reference: str, transcription: str, files_names: str, rm_punctuation: bool = True, ignore_case: bool = False, eli_gantu: bool = False) -> dict:
    """
    문자 오류율(CER) 계산을 최적화
    """
    # 타임스탬프 세그먼트 추출 (transcription에서)
    transcription_segments = extract_timestamp_segments(transcription)
    has_timestamps = len(transcription_segments) > 0
    
    # 텍스트 전처리
    refs = preprocess_text(reference, rm_punctuation, ignore_case, preserve_timestamps=False, eli_gantu=eli_gantu)
    trans = preprocess_text(transcription, rm_punctuation, ignore_case, preserve_timestamps=False, eli_gantu=eli_gantu)

    hits, substitutions, deletions, insertions, sub_chars, del_chars, ins_chars = measure_cer(refs, trans)
    incorrect = substitutions + deletions + insertions
    n = len(refs)
    
    # 분기 줄이기
    character_error_rate = incorrect / n if n > 0 else 0
    acc = 1 - character_error_rate
    
    # 글자별 타임스탬프 매핑 생성 (타임스탬프가 있는 경우)
    trans_char_timestamps = None
    if has_timestamps:
        trans_char_timestamps = create_char_timestamp_mapping(transcription_segments, trans, rm_punctuation, ignore_case, eli_gantu)
    
    # 시각적 비교 결과 생성 (타임스탬프 정보 포함 여부에 따라)
    if has_timestamps:
        visual_info = get_visual_comparison_with_timestamps(refs, trans, transcription_segments)
        # 글자별 타임스탬프 정보도 추가로 생성
        detailed_visual_info = get_visual_comparison_text(refs, trans, trans_char_timestamps)
        visual_info['char_positions'] = detailed_visual_info.get('char_positions', [])
    else:
        visual_info = get_visual_comparison_text(refs, trans, trans_char_timestamps)
    
    result = {
        'FileName': files_names,
        'sub': substitutions,
        'del': deletions,
        'ins': insertions,
        'n': n,
        'acc': acc,
        'cer': character_error_rate,
        'sub_chars': sub_chars,
        'del_chars': del_chars,
        'ins_chars': ins_chars,
        'visual_result': visual_info['visual_result'],  # 시각적 비교 결과
        'visual_tags': visual_info['visual_tags'],      # 태그 위치 정보
        'has_timestamps': has_timestamps
    }
    
    # 타임스탬프 정보가 있는 경우 추가
    if has_timestamps and 'timestamp_result' in visual_info:
        result['timestamp_result'] = visual_info['timestamp_result']
        result['timestamp_info'] = visual_info['timestamp_info']
    
    return result


def process_file_pair(args):
    """병렬 처리를 위한 파일 쌍 처리 함수"""
    file_name, refer_path, recog_path, remove_punctuation, ignore_case, eli_gantu = args
    
    try:
        # 파일 크기 체크 (너무 큰 파일은 건너뛰기)
        refer_size = os.path.getsize(refer_path)
        recog_size = os.path.getsize(recog_path)
        max_size = 10 * 1024 * 1024  # 10MB 제한
        
        if refer_size > max_size or recog_size > max_size:
            return (False, file_name, f"파일이 너무 큽니다. (참조: {refer_size/1024/1024:.1f}MB, 인식: {recog_size/1024/1024:.1f}MB)")
        
        # 파일 읽기 (에러 처리 강화)
        try:
            with open(refer_path, 'r', encoding='utf-8') as f:
                refer_text = f.read()
        except UnicodeDecodeError:
            # UTF-8 실패시 다른 인코딩 시도
            with open(refer_path, 'r', encoding='cp949') as f:
                refer_text = f.read()
        
        try:
            with open(recog_path, 'r', encoding='utf-8') as f:
                recog_text = f.read()
        except UnicodeDecodeError:
            # UTF-8 실패시 다른 인코딩 시도
            with open(recog_path, 'r', encoding='cp949') as f:
                recog_text = f.read()
        
        # 텍스트 길이 체크
        if len(refer_text) > 100000 or len(recog_text) > 100000:
            return (False, file_name, f"텍스트가 너무 깁니다. (참조: {len(refer_text)}자, 인식: {len(recog_text)}자)")
        
        # CER 계산
        result = get_cers(refer_text, recog_text, file_name, remove_punctuation, ignore_case, eli_gantu)
        
        return (True, file_name, result, refer_text, recog_text)
        
    except MemoryError:
        return (False, file_name, "메모리 부족")
    except Exception as e:
        return (False, file_name, str(e))


def process_file_pair_with_html(args):
    """파일 처리와 HTML 생성을 한 번에 수행하는 함수 (tt.py 방식 적용)"""
    file_name, refer_path, recog_path, remove_punctuation, ignore_case, eli_gantu = args[:6]
    output_dir = args[6] if len(args) > 6 else None
    
    # 기본 파일 처리
    result_tuple = process_file_pair(args[:6])
    
    if not result_tuple[0]:  # 실패한 경우
        return None
    
    success, file_name, result, refer_text, recog_text = result_tuple
    
    try:
        # HTML 파일 생성
        html_file_path = os.path.join(output_dir, f"{result['FileName']}.html")
        save_as_html(refer_text, recog_text, result, html_file_path)
        
        # 요약용 최소 정보만 저장 (메모리 절약)
        summary_result = {
            'FileName': result['FileName'],
            'sub': result['sub'],
            'del': result['del'],
            'ins': result['ins'],
            'n': result['n'],
            'acc': result['acc'],
            'cer': result['cer']
        }
        
        return summary_result, html_file_path
        
    except Exception as e:
        return None


def save_as_html(refer_text, recog_text, result, html_file_path):
    """비교 결과를 HTML 파일로 저장합니다."""
    # HTML 스타일 정의
    html_style = """
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        h1 { color: #333; }
        .container { display: flex; flex-direction: column; gap: 20px; }
        .summary { background-color: #f5f5f5; padding: 15px; border-radius: 5px; }
        .comparison { background-color: #fff; padding: 15px; border: 1px solid #ddd; border-radius: 5px; }
        .title { font-weight: bold; margin-bottom: 10px; }
        .result { font-family: monospace; white-space: pre-wrap; line-height: 1.5; }
        .orig-text, .recog-text { margin-top: 20px; }
        .diff { color: blue; font-weight: bold; }
        .delete { color: red; font-weight: bold; }
        .insert { color: green; font-weight: bold; }
        table { border-collapse: collapse; width: 100%; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
        
        /* 오디오 재생 관련 스타일 */
        .audio-controls { 
            background-color: #e9ecef; 
            padding: 15px; 
            border-radius: 5px; 
            margin-bottom: 20px;
            position: sticky;
            top: 0;
            z-index: 1000;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            border: 1px solid #dee2e6;
        }
        .audio-file-input { 
            margin-bottom: 10px; 
        }
        .audio-options {
            margin-bottom: 10px;
        }
        .audio-options label {
            display: flex;
            align-items: center;
            gap: 5px;
            font-size: 14px;
        }
        .timestamp-segment {
            cursor: pointer;
            padding: 2px 4px;
            border-radius: 3px;
            transition: background-color 0.2s;
        }
        .timestamp-segment:hover {
            background-color: #e6f3ff;
            border: 1px solid #0066cc;
        }
        .timestamp-segment.playing {
            background-color: #ffeb3b;
            border: 1px solid #f57f17;
        }
        .timestamp-tooltip {
            position: relative;
        }
        .timestamp-tooltip:hover::after {
            content: attr(data-tooltip);
            position: absolute;
            bottom: 125%;
            left: 50%;
            transform: translateX(-50%);
            background-color: #333;
            color: white;
            padding: 5px 10px;
            border-radius: 4px;
            white-space: nowrap;
            z-index: 1000;
            font-size: 12px;
        }
        .timestamp-tooltip:hover::before {
            content: '';
            position: absolute;
            bottom: 120%;
            left: 50%;
            transform: translateX(-50%);
            border: 5px solid transparent;
            border-top-color: #333;
            z-index: 1000;
        }
        #audioPlayer {
            width: 100%;
            margin-top: 10px;
        }
        .audio-status {
            margin-top: 10px;
            font-style: italic;
            color: #666;
        }
        
        /* 글자별 타임스탬프 툴팁 스타일 */
        .char-with-timestamp {
            position: relative;
            cursor: pointer;
        }
        .char-with-timestamp:hover {
            background-color: rgba(255, 235, 59, 0.3);
            border-radius: 2px;
        }
        .char-timestamp-tooltip {
            display: none !important;
        }
        
        /* 같은 시간대 글자들 하이라이트 */
        .segment-highlight {
            background-color: rgba(255, 235, 59, 0.6) !important;
        }
    </style>
    """
    
    # 시각적 비교 결과에서 HTML로 변환
    visual_result = result['visual_result']
    visual_tags = result['visual_tags']
    char_positions = result.get('char_positions', [])
    
    # 글자별 타임스탬프 정보를 포함한 HTML 생성
    html_visual_result = ""
    
    # 기본 태그 위치 정보를 이용하여 HTML에 스타일 적용
    all_tags = []
    for tag_type, tag_list in visual_tags.items():
        for start, end in tag_list:
            all_tags.append((start, end, tag_type))
    
    # 시작 위치 기준으로 정렬
    all_tags.sort(key=lambda x: x[0])
    
    # 타임스탬프가 있는 경우 간단한 매핑 방식 사용
    char_timestamp_map = {}
    if result.get('has_timestamps', False) and 'timestamp_info' in result:
        # 간단한 방법: 전체 텍스트에 대해 균등하게 타임스탬프 분배
        total_chars = len([c for c in visual_result if c not in '[]→'])
        char_count = 0
        
        for segment_info in result['timestamp_info']:
            segment_length = len(preprocess_text(segment_info['text'], preserve_timestamps=False))
            for i in range(segment_length):
                if char_count < total_chars:
                    char_timestamp_map[char_count] = {
                        'start': segment_info['start_time'],
                        'end': segment_info['end_time'],
                        'speaker': segment_info['speaker'],
                        'segment_text': segment_info['text']
                    }
                    char_count += 1
    
    # 실제 문자에 대한 인덱스 추적 (괄호와 화살표 제외)
    real_char_index = 0
    
    # 태그가 적용되지 않은 일반 텍스트 부분과 함께 HTML 생성
    current_pos = 0
    for start, end, tag_type in all_tags:
        # 태그 전 일반 텍스트 추가 (글자별 타임스탬프 체크)
        if start > current_pos:
            for i in range(current_pos, start):
                char = visual_result[i]
                if char in '[]→':  # 특수 문자는 그대로 출력 (카운트 안함)
                    html_visual_result += char
                else:  # 실제 문자만 카운트하고 타임스탬프 적용
                    if real_char_index in char_timestamp_map:
                        timestamp = char_timestamp_map[real_char_index]
                        tooltip_text = f"시간: {timestamp['start']:.2f}s - {timestamp['end']:.2f}s | 화자: {timestamp['speaker']}"
                        if 'segment_text' in timestamp:
                            tooltip_text += f" | 원문: '{timestamp['segment_text'][:20]}...'" if len(timestamp['segment_text']) > 20 else f" | 원문: '{timestamp['segment_text']}'"
                        
                        # 시간대 식별을 위한 data 속성 추가
                        time_key = f"{timestamp['start']:.2f}-{timestamp['end']:.2f}-{timestamp['speaker']}"
                        html_visual_result += f'<span class="char-with-timestamp" data-time-segment="{time_key}" data-start-time="{timestamp["start"]}" onmouseenter="highlightTimeSegment(\'{time_key}\')" onmouseleave="clearTimeSegmentHighlight()" onclick="playFromTimestamp({timestamp["start"]}, this)" title="{tooltip_text}">{char}</span>'
                    else:
                        html_visual_result += char
                    real_char_index += 1
        
        # 태그 부분 추가 (스타일 적용 + 글자별 타임스탬프 체크)
        tag_content = visual_result[start:end]
        tag_class = ""
        if tag_type == "diff_tags":
            tag_class = "diff"
        elif tag_type == "delete_tags":
            tag_class = "delete"
        elif tag_type == "insert_tags":
            tag_class = "insert"
        
        # 태그 내용도 글자별로 타임스탬프 체크
        tag_html_content = ""
        in_substitute_target = False  # substitute에서 → 뒤의 참조문 부분인지 추적
        
        for i in range(start, end):
            char = visual_result[i]
            
            if char == '→':  # substitute에서 화살표 발견
                in_substitute_target = True
                tag_html_content += char
            elif char in '[]':  # 대괄호는 그대로 출력 (카운트 안함)
                tag_html_content += char
            else:  # 실제 문자 처리
                should_apply_timestamp = False
                
                # 태그 종류별로 타임스탬프 적용 여부 결정
                if tag_type == "insert_tags":
                    # insert: 인식문에만 있는 글자이므로 타임스탬프 적용
                    should_apply_timestamp = True
                elif tag_type == "delete_tags":
                    # delete: 참조문에만 있는 글자이므로 타임스탬프 적용 안함
                    should_apply_timestamp = False
                elif tag_type == "diff_tags":
                    # substitute: 화살표 앞(인식문)만 타임스탬프 적용, 뒤(참조문)는 적용 안함
                    should_apply_timestamp = not in_substitute_target
                
                if should_apply_timestamp and real_char_index in char_timestamp_map:
                    timestamp = char_timestamp_map[real_char_index]
                    tooltip_text = f"시간: {timestamp['start']:.2f}s - {timestamp['end']:.2f}s | 화자: {timestamp['speaker']}"
                    if 'segment_text' in timestamp:
                        tooltip_text += f" | 원문: '{timestamp['segment_text'][:20]}...'" if len(timestamp['segment_text']) > 20 else f" | 원문: '{timestamp['segment_text']}'"
                    
                    # 시간대 식별을 위한 data 속성 추가
                    time_key = f"{timestamp['start']:.2f}-{timestamp['end']:.2f}-{timestamp['speaker']}"
                    tag_html_content += f'<span class="char-with-timestamp" data-time-segment="{time_key}" data-start-time="{timestamp["start"]}" onmouseenter="highlightTimeSegment(\'{time_key}\')" onmouseleave="clearTimeSegmentHighlight()" onclick="playFromTimestamp({timestamp["start"]}, this)" title="{tooltip_text}">{char}</span>'
                else:
                    tag_html_content += char
                
                # 인식문에서 나온 글자만 카운트 (delete 제외, substitute의 참조문 부분 제외)
                if should_apply_timestamp:
                    real_char_index += 1
        
        html_visual_result += f'<span class="{tag_class}">{tag_html_content}</span>'
        current_pos = end
    
    # 마지막 태그 이후 남은 텍스트 추가 (글자별 타임스탬프 체크)
    if current_pos < len(visual_result):
        for i in range(current_pos, len(visual_result)):
            char = visual_result[i]
            if char in '[]→':  # 특수 문자는 그대로 출력 (카운트 안함)
                html_visual_result += char
            else:  # 실제 문자만 카운트하고 타임스탬프 적용
                if real_char_index in char_timestamp_map:
                    timestamp = char_timestamp_map[real_char_index]
                    tooltip_text = f"시간: {timestamp['start']:.2f}s - {timestamp['end']:.2f}s | 화자: {timestamp['speaker']}"
                    if 'segment_text' in timestamp:
                        tooltip_text += f" | 원문: '{timestamp['segment_text'][:20]}...'" if len(timestamp['segment_text']) > 20 else f" | 원문: '{timestamp['segment_text']}'"
                    
                    # 시간대 식별을 위한 data 속성 추가
                    time_key = f"{timestamp['start']:.2f}-{timestamp['end']:.2f}-{timestamp['speaker']}"
                    html_visual_result += f'<span class="char-with-timestamp" data-time-segment="{time_key}" data-start-time="{timestamp["start"]}" onmouseenter="highlightTimeSegment(\'{time_key}\')" onmouseleave="clearTimeSegmentHighlight()" onclick="playFromTimestamp({timestamp["start"]}, this)" title="{tooltip_text}">{char}</span>'
                else:
                    html_visual_result += char
                real_char_index += 1
    
    # 오디오 컨트롤과 JavaScript 추가
    audio_controls = ""
    javascript = ""
    
    # 타임스탬프가 있는 경우 오디오 관련 기능 추가
    if result.get('has_timestamps', False):
        audio_controls = """
            <div class="audio-controls">
                <div class="title">오디오 재생 컨트롤</div>
                <div class="audio-file-input">
                    <label for="audioFile">음성 파일 선택:</label>
                    <input type="file" id="audioFile" accept="audio/*" onchange="loadAudioFile(this)">
                </div>
                <div class="audio-options">
                    <label for="repeatSegment">
                        <input type="checkbox" id="repeatSegment"> 세그먼트 반복 재생
                    </label>
                    <div style="margin-top: 10px; font-size: 14px; color: #666;">
                        💡 <strong>단축키:</strong><br>
                        • ESC: 재생/정지<br>
                        • F1: 2초 되감기<br>
                        • F4: 2초 빨리감기
                    </div>
                </div>
                <audio id="audioPlayer" controls style="display: none;">
                    Your browser does not support the audio element.
                </audio>
                <div id="audioStatus" class="audio-status">음성 파일을 선택해주세요.</div>
            </div>
        """
        
        javascript = """
        <script>
            let audioPlayer = null;
            let currentPlayingSegment = null;
            let currentSegmentEndTime = null;
            let segmentCheckInterval = null;
            let isRepeating = false;
            let timeLogInterval = null;
            let statusUpdateInterval = null;
            
            // 실시간 상태 업데이트 시작
            function startStatusUpdate() {
                const audioPlayer = document.getElementById('audioPlayer');
                const audioStatus = document.getElementById('audioStatus');
                
                if (statusUpdateInterval) {
                    clearInterval(statusUpdateInterval);
                }
                
                statusUpdateInterval = setInterval(() => {
                    if (audioPlayer && !audioPlayer.paused && !audioPlayer.ended) {
                        const currentTime = audioPlayer.currentTime;
                        const duration = audioPlayer.duration || 0;
                        audioStatus.textContent = `재생 중: ${currentTime.toFixed(2)}초 / ${duration.toFixed(2)}초`;
                    }
                }, 100); // 100ms = 0.1초마다 업데이트
            }
            
            // 실시간 상태 업데이트 중지
            function stopStatusUpdate() {
                if (statusUpdateInterval) {
                    clearInterval(statusUpdateInterval);
                    statusUpdateInterval = null;
                }
            }
            
            // 키보드 단축키 제어
            document.addEventListener('keydown', function(event) {
                const audioPlayer = document.getElementById('audioPlayer');
                const audioStatus = document.getElementById('audioStatus');
                
                if (event.key === 'Escape') {
                    // ESC: 재생/정지 토글
                    if (audioPlayer && audioPlayer.src) {
                        if (audioPlayer.paused) {
                            // 현재 정지 상태면 재생
                            audioPlayer.play().then(() => {
                                audioStatus.textContent = `ESC로 재생 시작: ${audioPlayer.currentTime.toFixed(2)}초부터`;
                                
                                // 실시간 상태 업데이트 시작
                                startStatusUpdate();
                                
                                // 시간 추적 시작
                                if (timeLogInterval) {
                                    clearInterval(timeLogInterval);
                                }
                                
                                timeLogInterval = setInterval(() => {
                                    if (!audioPlayer.paused && !audioPlayer.ended) {
                                        const currentTime = audioPlayer.currentTime;
                                        highlightCurrentTimeSegment(currentTime);
                                    }
                                }, 100);
                                
                            }).catch(error => {
                                audioStatus.textContent = '재생 오류가 발생했습니다.';
                            });
                        } else {
                            // 현재 재생 중이면 정지
                            audioPlayer.pause();
                            audioStatus.textContent = `ESC로 재생 정지됨: ${audioPlayer.currentTime.toFixed(2)}초`;
                            
                            // 실시간 상태 업데이트 중지
                            stopStatusUpdate();
                            
                            // 시간 추적 중지 및 하이라이트 제거
                            if (timeLogInterval) {
                                clearInterval(timeLogInterval);
                                timeLogInterval = null;
                            }
                            clearTimeSegmentHighlight();
                        }
                    } else {
                        if (audioStatus) {
                            audioStatus.textContent = '먼저 음성 파일을 선택해주세요.';
                        }
                    }
                    event.preventDefault();
                } else if (event.key === 'F1') {
                    // F1: 2초 되감기
                    if (audioPlayer && audioPlayer.src) {
                        const currentTime = audioPlayer.currentTime;
                        const newTime = Math.max(0, currentTime - 2); // 0초 미만으로 가지 않도록
                        audioPlayer.currentTime = newTime;
                        audioStatus.textContent = `F1로 2초 되감기: ${newTime.toFixed(1)}초`;
                        
                        // 재생 중이었다면 하이라이트 업데이트
                        if (!audioPlayer.paused) {
                            highlightCurrentTimeSegment(newTime);
                        }
                    } else {
                        if (audioStatus) {
                            audioStatus.textContent = '먼저 음성 파일을 선택해주세요.';
                        }
                    }
                    event.preventDefault();
                } else if (event.key === 'F4') {
                    // F4: 2초 빨리감기
                    if (audioPlayer && audioPlayer.src) {
                        const currentTime = audioPlayer.currentTime;
                        const duration = audioPlayer.duration || 0;
                        const newTime = Math.min(duration, currentTime + 2); // 전체 길이를 넘지 않도록
                        audioPlayer.currentTime = newTime;
                        audioStatus.textContent = `F4로 2초 빨리감기: ${newTime.toFixed(1)}초`;
                        
                        // 재생 중이었다면 하이라이트 업데이트
                        if (!audioPlayer.paused) {
                            highlightCurrentTimeSegment(newTime);
                        }
                    } else {
                        if (audioStatus) {
                            audioStatus.textContent = '먼저 음성 파일을 선택해주세요.';
                        }
                    }
                    event.preventDefault();
                }
            });
            
            function loadAudioFile(input) {
                const file = input.files[0];
                if (file) {
                    const audioPlayer = document.getElementById('audioPlayer');
                    const audioStatus = document.getElementById('audioStatus');
                    
                    const url = URL.createObjectURL(file);
                    audioPlayer.src = url;
                    audioPlayer.style.display = 'block';
                    audioStatus.textContent = `음성 파일 로드됨: ${file.name}`;
                    
                    // 오디오 재생이 끝났을 때 하이라이트 제거
                    audioPlayer.addEventListener('ended', function() {
                        stopSegmentPlayback();
                    });
                    
                    // 일시정지 시 시간 로깅 중지
                    audioPlayer.addEventListener('pause', function() {
                        // 실시간 상태 업데이트 중지
                        stopStatusUpdate();
                        
                        if (timeLogInterval) {
                            clearInterval(timeLogInterval);
                            timeLogInterval = null;
                        }
                        // 하이라이트도 제거
                        clearTimeSegmentHighlight();
                        // 일시정지 상태 표시
                        audioStatus.textContent = `일시정지됨: ${audioPlayer.currentTime.toFixed(2)}초`;
                    });
                    
                    // 재생 중 시간 체크 및 실시간 위치 표시
                    audioPlayer.addEventListener('timeupdate', function() {
                        // 실시간 재생 위치 표시
                        if (!audioPlayer.paused && !audioPlayer.ended) {
                            const currentTime = audioPlayer.currentTime;
                            const duration = audioPlayer.duration || 0;
                            audioStatus.textContent = `재생 중: ${currentTime.toFixed(2)}초 / ${duration.toFixed(2)}초`;
                        }
                        
                        // 반복 재생 체크
                        if (currentSegmentEndTime && isRepeating && audioPlayer.currentTime >= currentSegmentEndTime) {
                            // 반복 재생: 시작 시간으로 되돌리기
                            const startTime = parseFloat(currentPlayingSegment.getAttribute('data-start-time'));
                            audioPlayer.currentTime = startTime;
                        }
                    });
                }
            }
            
            function playFromTimestamp(startTime, element) {
                const audioPlayer = document.getElementById('audioPlayer');
                const audioStatus = document.getElementById('audioStatus');
                
                if (!audioPlayer.src) {
                    audioStatus.textContent = '먼저 음성 파일을 선택해주세요.';
                    return;
                }
                
                // 이전 재생 중인 세그먼트 하이라이트 제거
                if (currentPlayingSegment) {
                    currentPlayingSegment.classList.remove('playing');
                }
                
                // 현재 세그먼트 하이라이트 추가
                element.classList.add('playing');
                currentPlayingSegment = element;
                
                // 지정된 시간부터 재생
                audioPlayer.currentTime = startTime;
                
                audioPlayer.play().then(() => {
                    audioStatus.textContent = `재생 시작: ${startTime.toFixed(2)}초부터`;
                    
                    // 실시간 상태 업데이트 시작
                    startStatusUpdate();
                    
                    // 0.1초마다 현재 재생 시간에 해당하는 글자들 하이라이트
                    if (timeLogInterval) {
                        clearInterval(timeLogInterval);
                    }
                    
                    timeLogInterval = setInterval(() => {
                        if (!audioPlayer.paused && !audioPlayer.ended) {
                            const currentTime = audioPlayer.currentTime;
                            
                            // 현재 재생 시간에 해당하는 글자들 하이라이트
                            highlightCurrentTimeSegment(currentTime);
                        }
                    }, 100); // 100ms = 0.1초
                    
                }).catch(error => {
                    audioStatus.textContent = '재생 오류가 발생했습니다.';
                });
            }
            
            function stopSegmentPlayback() {
                if (currentPlayingSegment) {
                    currentPlayingSegment.classList.remove('playing');
                    currentPlayingSegment = null;
                }
                currentSegmentEndTime = null;
                isRepeating = false;
                
                // 실시간 상태 업데이트 중지
                stopStatusUpdate();
                
                // 시간 로깅 중지
                if (timeLogInterval) {
                    clearInterval(timeLogInterval);
                    timeLogInterval = null;
                }
                
                // 모든 하이라이트 제거
                clearTimeSegmentHighlight();
                
                const audioStatus = document.getElementById('audioStatus');
                const audioPlayer = document.getElementById('audioPlayer');
                if (audioPlayer) {
                    audioStatus.textContent = `재생 중지됨: ${audioPlayer.currentTime.toFixed(2)}초`;
                } else {
                    audioStatus.textContent = '재생 중지됨';
                }
            }
            
            // 시간대 범위 하이라이트 함수
            function highlightTimeSegment(timeKey) {
                try {
                    // 해당 시간대의 모든 요소들을 하이라이트
                    const sameTimeElements = document.querySelectorAll(`[data-time-segment="${timeKey}"]`);
                    
                    if (sameTimeElements.length === 0) return;
                    
                    // 시각적 비교 결과 영역의 모든 span 찾기
                    const resultDiv = document.querySelector('.comparison .result');
                    if (!resultDiv) return;
                    
                    const allSpans = resultDiv.querySelectorAll('span');
                    const spanArray = Array.from(allSpans);
                    
                    // 첫 번째와 마지막 타임스탬프 요소의 인덱스 찾기
                    const firstIndex = spanArray.indexOf(sameTimeElements[0]);
                    const lastIndex = spanArray.indexOf(sameTimeElements[sameTimeElements.length - 1]);
                    
                    // 범위 내의 모든 span 요소 하이라이트
                    if (firstIndex !== -1 && lastIndex !== -1) {
                        for (let i = firstIndex; i <= lastIndex; i++) {
                            const span = spanArray[i];
                            if (span) {
                                span.classList.add('segment-highlight');
                            }
                        }
                    }
                } catch (error) {
                    // 에러가 발생해도 기본 하이라이트는 동작
                    const sameTimeElements = document.querySelectorAll(`[data-time-segment="${timeKey}"]`);
                    sameTimeElements.forEach(el => {
                        el.classList.add('segment-highlight');
                    });
                }
            }
            
            function clearTimeSegmentHighlight() {
                // 모든 하이라이트 제거
                const elements = document.querySelectorAll('.segment-highlight');
                elements.forEach(el => {
                    el.classList.remove('segment-highlight');
                });
            }
            
            // 현재 재생 시간에 해당하는 세그먼트 하이라이트
            function highlightCurrentTimeSegment(currentTime) {
                // 먼저 모든 하이라이트 제거
                clearTimeSegmentHighlight();
                
                // 현재 시간에 해당하는 타임스탬프 요소 찾기
                const timestampElements = document.querySelectorAll('.char-with-timestamp');
                
                timestampElements.forEach(element => {
                    const timeSegment = element.getAttribute('data-time-segment');
                    if (timeSegment) {
                        const [start, end, speaker] = timeSegment.split('-');
                        const startTime = parseFloat(start);
                        const endTime = parseFloat(end);
                        
                        // 현재 시간이 이 세그먼트 범위에 있는지 확인
                        if (currentTime >= startTime && currentTime < endTime) {
                            // 이 시간대의 모든 글자들 하이라이트
                            highlightTimeSegment(timeSegment);
                            return; // 첫 번째 매칭되는 세그먼트만 하이라이트
                        }
                    }
                });
            }
        </script>
        """
        
        # 타임스탬프가 있는 텍스트 표시
        if 'timestamp_result' in result:
            timestamp_html = result['timestamp_result']
            # 타임스탬프 세그먼트에 클릭 이벤트와 툴팁 추가
            if 'timestamp_info' in result:
                for segment_info in result['timestamp_info']:
                    start_time = segment_info['start_time']
                    end_time = segment_info['end_time']
                    speaker = segment_info['speaker']
                    text = segment_info['text']
                    
                    # 툴팁 정보 생성
                    tooltip = f"시간: {start_time:.2f}s - {end_time:.2f}s | 화자: {speaker} | 클릭하여 재생"
                    
                    # 기존 span을 클릭 가능한 형태로 변경
                    old_span = f'<span class="timestamp-segment" data-start="{start_time}" data-end="{end_time}" data-speaker="{speaker}">{text}</span>'
                    new_span = f'<span class="timestamp-segment timestamp-tooltip" data-start="{start_time}" data-end="{end_time}" data-speaker="{speaker}" data-tooltip="{tooltip}" onclick="playFromTimestamp({start_time}, this)">{text}</span>'
                    timestamp_html = timestamp_html.replace(old_span, new_span)
    
    # HTML 생성
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>텍스트 비교 결과: {result['FileName']}</title>
        {html_style}
    </head>
    <body>
        <h1>텍스트 비교 결과: {result['FileName']}</h1>
        <div class="container">
            {audio_controls}
            
            <div class="summary">
                <div class="title">요약</div>
                <table>
                    <tr><th>항목</th><th>값</th></tr>
                    <tr><td>파일명</td><td>{result['FileName']}</td></tr>
                    <tr><td>대체(Substitutions)</td><td>{result['sub']}</td></tr>
                    <tr><td>삭제(Deletions)</td><td>{result['del']}</td></tr>
                    <tr><td>삽입(Insertions)</td><td>{result['ins']}</td></tr>
                    <tr><td>총 문자 수</td><td>{result['n']}</td></tr>
                    <tr><td>정확도(Accuracy)</td><td>{result['acc']:.4f} ({result['acc']*100:.2f}%)</td></tr>
                    <tr><td>문자 오류율(CER)</td><td>{result['cer']:.4f} ({result['cer']*100:.2f}%)</td></tr>
                    <tr><td>타임스탬프 포함</td><td>{'예' if result.get('has_timestamps', False) else '아니오'}</td></tr>
                </table>
            </div>
            
            <div class="comparison">
                <div class="title">시각적 비교 결과</div>
                <div class="result">{html_visual_result}</div>
            </div>
            
            {f'<div class="comparison"><div class="title">타임스탬프별 인식 텍스트 (클릭하여 재생)</div><div class="result">{timestamp_html}</div></div>' if result.get('has_timestamps', False) and 'timestamp_result' in result else ''}
            
            <div class="orig-text">
                <div class="title">원본 텍스트</div>
                <pre>{refer_text}</pre>
            </div>
            
            <div class="recog-text">
                <div class="title">인식 텍스트</div>
                <pre>{recog_text}</pre>
            </div>
        </div>
        {javascript}
    </body>
    </html>
    """
    
    # HTML 파일 저장
    with open(html_file_path, 'w', encoding='utf-8') as f:
        f.write(html_content)


def create_summary_html(comparison_results, result_folder):
    """모든 파일의 결과를 요약한 HTML 파일을 생성합니다."""
    if not comparison_results or not result_folder:
        return
    
    html_style = """
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        h1 { color: #333; }
        table { border-collapse: collapse; width: 100%; margin-top: 20px; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
        tr:hover { background-color: #f5f5f5; }
        .summary { background-color: #e9f7ef; font-weight: bold; }
    </style>
    """
    
    # HTML 내용 시작
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>텍스트 비교 결과 요약</title>
        {html_style}
    </head>
    <body>
        <h1>텍스트 비교 결과 요약</h1>
        <p>총 {len(comparison_results)}개 파일의 비교 결과</p>
        <table>
            <tr>
                <th>파일명</th>
                <th>대체</th>
                <th>삭제</th>
                <th>삽입</th>
                <th>총 문자 수</th>
                <th>정확도(%)</th>
                <th>CER(%)</th>
                <th>보기</th>
            </tr>
    """
    
    # 각 파일의 결과 추가
    for result in comparison_results:
        html_content += f"""
            <tr>
                <td>{result['FileName']}</td>
                <td>{result['sub']}</td>
                <td>{result['del']}</td>
                <td>{result['ins']}</td>
                <td>{result['n']}</td>
                <td>{result['acc']*100:.2f}%</td>
                <td>{result['cer']*100:.2f}%</td>
                <td><a href="{result['FileName']}.html" target="_blank">상세 보기</a></td>
            </tr>
        """
    
    # 요약 정보 계산
    total_files = len(comparison_results)
    total_sub = sum(r['sub'] for r in comparison_results)
    total_del = sum(r['del'] for r in comparison_results)
    total_ins = sum(r['ins'] for r in comparison_results)
    total_n = sum(r['n'] for r in comparison_results)
    
    # 전체 기준 정확도와 CER 계산
    total_errors = total_sub + total_del + total_ins
    overall_cer = total_errors / total_n if total_n > 0 else 0
    overall_acc = 1 - overall_cer
    
    # 요약 행 추가
    html_content += f"""
            <tr class="summary">
                <td>요약 (총 {total_files}개)</td>
                <td>{total_sub}</td>
                <td>{total_del}</td>
                <td>{total_ins}</td>
                <td>{total_n}</td>
                <td>{overall_acc*100:.2f}%</td>
                <td>{overall_cer*100:.2f}%</td>
                <td>-</td>
            </tr>
        </table>
        <p>생성일시: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
    </body>
    </html>
    """
    
    # 요약 HTML 파일 저장
    summary_file_path = os.path.join(result_folder, "summary.html")
    with open(summary_file_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"요약 정보 HTML 생성 완료: {summary_file_path}")


def create_summary_csv(comparison_results, result_folder):
    """모든 파일의 결과를 요약한 CSV 파일을 생성합니다."""
    if not comparison_results or not result_folder:
        return
    
    # CSV 파일 경로
    csv_file_path = os.path.join(result_folder, "summary.csv")
    
    try:
        # UTF-8 BOM 추가로 엑셀에서 한글 깨짐 방지
        with open(csv_file_path, 'w', newline='', encoding='utf-8-sig') as csvfile:
            # CSV 헤더 정의
            fieldnames = [
                '파일명', '대체(Substitutions)', '삭제(Deletions)', '삽입(Insertions)', 
                '총_문자_수', '정확도(%)', 'CER(%)', '생성일시'
            ]
            
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            # 헤더 쓰기
            writer.writeheader()
            
            # 각 파일의 결과 쓰기
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for result in comparison_results:
                writer.writerow({
                    '파일명': result['FileName'],
                    '대체(Substitutions)': result['sub'],
                    '삭제(Deletions)': result['del'],
                    '삽입(Insertions)': result['ins'],
                    '총_문자_수': result['n'],
                    '정확도(%)': f"{result['acc']*100:.2f}",
                    'CER(%)': f"{result['cer']*100:.2f}",
                    '생성일시': current_time
                })
            
            # 요약 정보 계산
            total_files = len(comparison_results)
            total_sub = sum(r['sub'] for r in comparison_results)
            total_del = sum(r['del'] for r in comparison_results)
            total_ins = sum(r['ins'] for r in comparison_results)
            total_n = sum(r['n'] for r in comparison_results)
            
            # 전체 기준 정확도와 CER 계산
            total_errors = total_sub + total_del + total_ins
            overall_cer = total_errors / total_n if total_n > 0 else 0
            overall_acc = 1 - overall_cer
            
            # 요약 행 추가
            writer.writerow({
                '파일명': f'=== 요약 (총 {total_files}개 파일) ===',
                '대체(Substitutions)': total_sub,
                '삭제(Deletions)': total_del,
                '삽입(Insertions)': total_ins,
                '총_문자_수': total_n,
                '정확도(%)': f"{overall_acc*100:.2f}",
                'CER(%)': f"{overall_cer*100:.2f}",
                '생성일시': current_time
            })
        
        print(f"요약 정보 CSV 생성 완료: {csv_file_path}")
        
    except Exception as e:
        print(f"CSV 파일 생성 중 오류 발생: {str(e)}")


def setup_logging():
    """로깅 설정"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler()
        ]
    )

def main():
    parser = argparse.ArgumentParser(description='텍스트 비교 도구 - Linux 버전')
    parser.add_argument('-A', '--reference', required=True, help='참조 텍스트 폴더 경로')
    parser.add_argument('-B', '--recognition', required=True, help='인식 텍스트 폴더 경로')
    parser.add_argument('-o', '--output', required=True, help='HTML 저장될 폴더 경로')
    parser.add_argument('-w', '--workers', type=int, default=multiprocessing.cpu_count(), 
                       help=f'워커 수 (기본값: {multiprocessing.cpu_count()})')
    parser.add_argument('--remove-punctuation', action='store_true', default=True,
                       help='구두점 제거 (기본값: True)')
    parser.add_argument('--ignore-case', action='store_true', default=True,
                       help='대소문자 무시 (기본값: True)')
    parser.add_argument('--eli-gantu', action='store_true', default=False,
                       help='단독 감탄사(이/그/저/뭐/어) 제거 (기본값: False)')
    
    args = parser.parse_args()
    
    # 로깅 설정
    setup_logging()
    
    # 폴더 존재 확인
    if not os.path.exists(args.reference):
        print(f"오류: 참조 텍스트 폴더가 존재하지 않습니다: {args.reference}")
        sys.exit(1)
    
    if not os.path.exists(args.recognition):
        print(f"오류: 인식 텍스트 폴더가 존재하지 않습니다: {args.recognition}")
        sys.exit(1)
    
    # 출력 폴더 생성
    os.makedirs(args.output, exist_ok=True)
    
    logging.info(f"참조 텍스트 폴더: {args.reference}")
    logging.info(f"인식 텍스트 폴더: {args.recognition}")
    logging.info(f"출력 폴더: {args.output}")
    logging.info(f"워커 수: {args.workers}")
    logging.info(f"구두점 제거: {args.remove_punctuation}")
    logging.info(f"대소문자 무시: {args.ignore_case}")
    logging.info(f"감탄사 제거: {args.eli_gantu}")
    logging.info("-" * 50)
    
    # 폴더 내 파일 목록 가져오기
    refer_files = {f: os.path.join(args.reference, f) for f in os.listdir(args.reference) 
                  if os.path.isfile(os.path.join(args.reference, f)) and f.endswith('.txt')}
    recog_files = {f: os.path.join(args.recognition, f) for f in os.listdir(args.recognition) 
                  if os.path.isfile(os.path.join(args.recognition, f)) and f.endswith('.txt')}
    
    # 같은 이름의 파일 찾기
    common_files = set(refer_files.keys()) & set(recog_files.keys())
    
    if not common_files:
        logging.error("오류: 두 폴더에 같은 이름의 .txt 파일이 없습니다.")
        sys.exit(1)
    
    # 파일 목록 정렬
    sorted_common_files = sorted(list(common_files))
    logging.info(f"총 {len(sorted_common_files)}개의 파일을 비교합니다.")
    
    # 병렬 처리를 위한 작업 목록 생성
    tasks = []
    for file_name in sorted_common_files:
        tasks.append((
            file_name,
            refer_files[file_name],
            recog_files[file_name],
            args.remove_punctuation,
            args.ignore_case,
            args.eli_gantu,
            args.output  # HTML 출력 디렉토리 추가
        ))
    
    logging.info(f"{args.workers}개의 워커로 병렬 처리 시작...")
    
    # 병렬 처리 실행 - tt.py 방식 적용
    comparison_results = []
    
    try:
        # 멀티프로세싱 Pool 생성 및 작업 분배 (tt.py 방식)
        with multiprocessing.Pool(processes=args.workers) as pool:
            # tqdm으로 진행 상황 표시
            results = list(tqdm.tqdm(
                pool.imap(process_file_pair_with_html, tasks),
                total=len(tasks),
                desc="파일 비교 중"
            ))
        
        # 결과 처리
        processed_count = 0
        for result_data in results:
            if result_data is not None:
                summary_result, html_path = result_data
                comparison_results.append(summary_result)
                processed_count += 1
                logging.info(f"HTML 생성: {html_path}")
                
                # 주기적으로 가비지 컬렉션 수행
                if processed_count % 5 == 0:
                    gc.collect()
                    
    except Exception as e:
        logging.error(f"병렬 처리 중 심각한 오류 발생: {str(e)}")
        logging.info("단일 스레드 모드로 전환합니다...")
        
        # 단일 스레드 모드로 fallback
        for task in tqdm.tqdm(tasks, desc="단일 스레드 처리 중"):
            try:
                result_data = process_file_pair_with_html(task)
                if result_data is not None:
                    summary_result, html_path = result_data
                    comparison_results.append(summary_result)
                    processed_count += 1
                    logging.info(f"HTML 생성: {html_path}")
                    
                    # 주기적으로 가비지 컬렉션 수행
                    if processed_count % 5 == 0:
                        gc.collect()
                    
            except Exception as e:
                logging.error(f"오류: 파일 '{task[0]}' 처리 중 예외 발생 - {str(e)}")
                continue
    
    logging.info(f"\n비교 완료! 총 {processed_count}개 파일 처리됨")
    
    # 요약 HTML 및 CSV 파일 생성
    if comparison_results:
        create_summary_html(comparison_results, args.output)
        create_summary_csv(comparison_results, args.output)
        
        # 요약 정보 출력
        logging.info("\n" + "="*50)
        logging.info("비교 결과 요약")
        logging.info("="*50)
        
        total_files = len(comparison_results)
        total_sub = sum(r['sub'] for r in comparison_results)
        total_del = sum(r['del'] for r in comparison_results)
        total_ins = sum(r['ins'] for r in comparison_results)
        total_n = sum(r['n'] for r in comparison_results)
        
        total_errors = total_sub + total_del + total_ins
        overall_cer = total_errors / total_n if total_n > 0 else 0
        overall_acc = 1 - overall_cer
        
        logging.info(f"총 파일 수: {total_files}")
        logging.info(f"총 대체: {total_sub}")
        logging.info(f"총 삭제: {total_del}")
        logging.info(f"총 삽입: {total_ins}")
        logging.info(f"총 문자 수: {total_n}")
        logging.info(f"전체 정확도: {overall_acc:.4f} ({overall_acc*100:.2f}%)")
        logging.info(f"전체 CER: {overall_cer:.4f} ({overall_cer*100:.2f}%)")
        
        logging.info(f"\n결과 파일들이 {args.output} 폴더에 저장되었습니다.")
        logging.info("summary.html 파일을 열어서 전체 결과를 확인할 수 있습니다.")
        logging.info("summary.csv 파일에서 요약 데이터를 스프레드시트로 확인할 수 있습니다.")
    else:
        logging.info("처리된 파일이 없습니다.")


if __name__ == "__main__":
    main()
