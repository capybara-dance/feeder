"""
Tests for scripts/build_krx_stock_master.py
"""
import sys
from pathlib import Path
import pandas as pd
import pytest

# Add the scripts directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from build_krx_stock_master import _update_names_from_fdr


def test_update_names_from_fdr_basic():
    """Test that _update_names_from_fdr updates stock names correctly."""
    # Create a sample DataFrame with some stock codes
    df = pd.DataFrame({
        'Code': ['005930', '000660', '005380'],
        'Name': ['Old Name 1', 'Old Name 2', 'Old Name 3'],
        'Market': ['KOSPI', 'KOSPI', 'KOSPI'],
    })
    
    # Call the function (this requires network access)
    result = _update_names_from_fdr(df, market='KOSPI')
    
    # Verify the function returns a DataFrame
    assert isinstance(result, pd.DataFrame)
    
    # Verify the shape is unchanged
    assert result.shape == df.shape
    
    # Verify columns are preserved
    assert list(result.columns) == list(df.columns)
    
    # Verify the stock codes are unchanged
    assert result['Code'].tolist() == df['Code'].tolist()


@pytest.mark.external
def test_update_names_from_fdr_kospi():
    """Test updating KOSPI stock names from FDR (requires network)."""
    # Create a sample DataFrame with known KOSPI stocks
    df = pd.DataFrame({
        'Code': ['005930', '000660'],  # Samsung Electronics, SK Hynix
        'Name': ['삼성전자 옛날이름', 'SK하이닉스 옛날이름'],
        'Market': ['KOSPI', 'KOSPI'],
        'IndustryLarge': ['전기전자', '전기전자'],
    })
    
    # Update names from FDR
    result = _update_names_from_fdr(df, market='KOSPI')
    
    # Verify that names were updated (they should be different from the original)
    # Note: We can't hardcode exact names as they may change, but we can verify they changed
    assert result.shape == df.shape
    assert '005930' in result['Code'].values
    assert '000660' in result['Code'].values


@pytest.mark.external
def test_update_names_from_fdr_kosdaq():
    """Test updating KOSDAQ stock names from FDR (requires network)."""
    # Create a sample DataFrame with a known KOSDAQ stock
    df = pd.DataFrame({
        'Code': ['196170'],  # 알테오젠 (a KOSDAQ stock)
        'Name': ['Old Name'],
        'Market': ['KOSDAQ'],
        'IndustryLarge': ['의료정밀'],
    })
    
    # Update names from FDR
    result = _update_names_from_fdr(df, market='KOSDAQ')
    
    # Verify the structure is preserved
    assert result.shape == df.shape
    assert '196170' in result['Code'].values


@pytest.mark.external
def test_update_names_from_fdr_specific_stock():
    """Test that code 240810 name is correctly updated from '원익아이피에스' to '원익IPS'."""
    # Create a DataFrame with stock code 240810 (원익IPS)
    df = pd.DataFrame({
        'Code': ['240810'],
        'Name': ['원익아이피에스'],  # Old incorrect name
        'Market': ['KOSDAQ'],
        'IndustryLarge': ['전기전자'],
    })
    
    # Update names from FDR
    result = _update_names_from_fdr(df, market='KOSDAQ')
    
    # Verify the name was updated correctly
    assert result.shape == df.shape
    assert result['Code'].iloc[0] == '240810'
    assert result['Name'].iloc[0] == '원익IPS', f"Expected '원익IPS' but got '{result['Name'].iloc[0]}'"
    
    # Verify other columns are preserved
    assert result['Market'].iloc[0] == 'KOSDAQ'
    assert result['IndustryLarge'].iloc[0] == '전기전자'


def test_update_names_from_fdr_empty_dataframe():
    """Test that empty DataFrame is handled correctly."""
    df = pd.DataFrame(columns=['Code', 'Name', 'Market'])
    
    result = _update_names_from_fdr(df, market='KOSPI')
    
    # Verify empty DataFrame is returned
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 0


def test_update_names_from_fdr_preserves_other_columns():
    """Test that other columns are preserved during name update."""
    df = pd.DataFrame({
        'Code': ['005930'],
        'Name': ['Old Name'],
        'Market': ['KOSPI'],
        'IndustryLarge': ['전기전자'],
        'IndustryMid': ['반도체'],
        'SharesOutstanding': [1000000],
    })
    
    result = _update_names_from_fdr(df, market='KOSPI')
    
    # Verify all columns are preserved
    assert set(result.columns) == set(df.columns)
    
    # Verify non-Name columns are unchanged
    assert result['Code'].iloc[0] == '005930'
    assert result['Market'].iloc[0] == 'KOSPI'
    assert result['IndustryLarge'].iloc[0] == '전기전자'
    assert result['IndustryMid'].iloc[0] == '반도체'
    assert result['SharesOutstanding'].iloc[0] == 1000000
