/**
 * 期望值与实测值的比对。
 *
 * 借自 tian-yu200/dsh-solidworks 的验收思路：模型在批准方案里写死包围盒，
 * 建完按实测逐轴比对；每轴容差 max(1 mm, 2%)。无 expect 时只能算
 * measured-only，不能算 verified。
 *
 * @module dsh-cad-workbench/lib/plan
 */

const ABS_TOL_MM = 1.0
const REL_TOL = 0.02

/** 单轴容差。 */
export function axisTolerance(expected) {
  return Math.max(ABS_TOL_MM, Math.abs(Number(expected) || 0) * REL_TOL)
}

/**
 * 比较期望与实测。
 * @param {{bbox_mm?: number[], volume_mm3?: number, feature_count?: number}} expect
 * @param {{bbox_mm?: number[], volume_mm3?: number, feature_count?: number}} measured
 * @returns {{status: 'verified'|'mismatch'|'measured-only', checks: object[], failures: string[]}}
 */
export function compareExpect(expect, measured) {
  const checks = []
  const failures = []
  const source = expect ?? {}

  if (Array.isArray(source.bbox_mm) && source.bbox_mm.length === 3) {
    const actual = Array.isArray(measured?.bbox_mm) ? measured.bbox_mm : null
    if (!actual) {
      failures.push('缺少实测包围盒，无法比对')
    } else {
      const axes = ['x', 'y', 'z']
      for (let index = 0; index < 3; index += 1) {
        const expected = Number(source.bbox_mm[index])
        const value = Number(actual[index])
        const tol = axisTolerance(expected)
        const delta = value - expected
        const pass = Number.isFinite(value) && Math.abs(delta) <= tol
        checks.push({
          id: `bbox_${axes[index]}`,
          expected_mm: round(expected),
          actual_mm: round(value),
          delta_mm: round(delta),
          tolerance_mm: round(tol),
          status: pass ? 'PASS' : 'FAIL',
        })
        if (!pass) failures.push(`包围盒 ${axes[index].toUpperCase()} 期望 ${round(expected)} mm，实测 ${round(value)} mm（容差 ${round(tol)} mm）`)
      }
    }
  }

  if (Number.isFinite(Number(source.volume_mm3)) && Number(source.volume_mm3) > 0) {
    const expected = Number(source.volume_mm3)
    const value = Number(measured?.volume_mm3)
    if (!Number.isFinite(value)) {
      failures.push('缺少实测体积（massprops 未启用或不可用），无法比对体积')
    } else {
      const tol = Math.max(1, expected * REL_TOL)
      const pass = Math.abs(value - expected) <= tol
      checks.push({
        id: 'volume',
        expected_mm3: round(expected),
        actual_mm3: round(value),
        delta_mm3: round(value - expected),
        tolerance_mm3: round(tol),
        status: pass ? 'PASS' : 'FAIL',
      })
      if (!pass) failures.push(`体积期望 ${round(expected)} mm³，实测 ${round(value)} mm³`)
    }
  }

  if (Number.isFinite(Number(source.feature_count)) && Number(source.feature_count) > 0) {
    const expected = Number(source.feature_count)
    const value = Number(measured?.feature_count)
    if (!Number.isFinite(value)) {
      failures.push('缺少实测特征数，无法比对')
    } else if (value !== expected) {
      failures.push(`特征数期望 ${expected}，实测 ${value}`)
      checks.push({ id: 'feature_count', expected, actual: value, status: 'FAIL' })
    } else {
      checks.push({ id: 'feature_count', expected, actual: value, status: 'PASS' })
    }
  }

  if (failures.length) return { status: 'mismatch', checks, failures }
  if (!checks.length) return { status: 'measured-only', checks, failures }
  return { status: 'verified', checks, failures }
}

function round(value) {
  if (!Number.isFinite(value)) return null
  return Math.round(value * 1000) / 1000
}
