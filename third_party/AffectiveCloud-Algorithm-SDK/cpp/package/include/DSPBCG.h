//
// Created by Enter M1 on 2023/8/28.
//

#ifndef AFFECTIVECPP_DSPBCG_H
#define AFFECTIVECPP_DSPBCG_H
#include "TypeDefine.h"
#include <climits>

namespace basic::dsp
{
    /// 峰值检测
    struct PeakIntervalResult {
        int intervalNum; // 间隔数
        int intervalSum; // 间隔和
        std::vector<int> validIntervalList; // 有效间隔列表
    };

    /// 峰值检测
    /// \param wave 波形（用于计算峰值点的波形）
    /// \return 峰值点索引
    vectori peakDetect(const vectord & wave);

    /// 峰值间隔计算
    /// \param peakIndexList 峰值索引序列
    /// \param validRange 有效区间（如果峰值索引超过该长度，则去除该峰值进行计算，避免尾部波形振荡导致峰值不准）
    /// \return 峰值间隔总数，峰值间隔总长度，有效间隔序列
    PeakIntervalResult peakIntervalCal(const std::vector<int>& peakIndexList, std::pair<int, int> validRange = {0, INT_MAX});

    /// 峰值数量校正（补峰与除峰）
    /// \param peakIndexList 峰值索引序列
    /// \param refInterval 峰值间隔参考值（用于进行峰值检验）
    /// \param sigmaL 有效间隔判定左邻域（峰值点参考间隔的倍数，0~1，默认0.3）
    /// \param sigmaR 有效间隔判定右邻域（峰值点参考间隔的倍数，0~1，默认0.5）
    /// \return 校正后的峰值索引序列
    std::vector<int> peakNumAdjust(const std::vector<int>& peakIndexList, int refInterval = -1,
                                   float sigmaL = 0.3f, float sigmaR = 0.5f);

    /// 峰值位置校正（最近邻或最大值）
    /// \param wave 波形（在该波形中搜寻最佳的峰值点位置）
    /// \param potentialPeakIndexList 潜在峰值点索引序列（校正前的峰值点索引）
    /// \param thr 峰值后验阈值
    /// \param strategy 校正策略（最近邻峰值nn/邻域内最大峰值mp）
    /// \param sigma 邻域大小（峰值点平均间隔的倍数，默认0.2）
    /// \return 校正后的峰值点索引
    std::vector<int> peakPosAdjust(const std::vector<double>& wave, const std::vector<int>& potentialPeakIndexList,
                                   double thr = -std::numeric_limits<double>::infinity(), const std::string& strategy = "nn",
                                   float sigma = 0.2f);

    /// 伪峰剔除
    /// \param wave 波形（在该波形中峰值点）
    /// \param peakIndexList 峰值点索引序列
    /// \param reversePeakIndexList 反向峰值点索引序列（用于计算峰值点的高度）
    /// \param rateThr 比例阈值（伪峰与相邻峰值差值的判断阈值，相邻峰值的倍数）
    /// \param biDirCheck 是否双向校验（峰值高度是否需要同时满足左右邻）
    /// \return 校正后的峰值点索引
    std::vector<int> fakePeakDel(const std::vector<double>& wave, const std::vector<int>& peakIndexList,
                                 const std::vector<int>& reversePeakIndexList, float rateThr = 0.6f,
                                 bool biDirCheck = false);
}

#endif //AFFECTIVECPP_DSPBCG_H
