//
// Created by Enter M1 on 2023/8/28.
//

#include "DSPBCG.h"
#include "Common.h"
#include <numeric>
#include <algorithm>
#include <cmath>
#include <stdexcept>
namespace basic::dsp
{

    /// 峰值检测
    /// \param wave 波形（用于计算峰值点的波形）
    /// \return 峰值点索引
    vectori peakDetect(const vectord & wave)
    {
        vectori peakIndexList;
        vectord diff1Wave = diff1Cal(wave);
        for (std::size_t i = 0; i < diff1Wave.size() - 1; ++i)
        {
            if (diff1Wave[i + 1] < 0 && diff1Wave[i] >= 0)
                peakIndexList.push_back(i);
        }
        return peakIndexList;
    }

    /// 峰值间隔计算
    /// \param peakIndexList 峰值索引序列
    /// \param validRange 有效区间（如果峰值索引超过该长度，则去除该峰值进行计算，避免尾部波形振荡导致峰值不准）
    /// \return 峰值间隔总数，峰值间隔总长度，有效间隔序列
    PeakIntervalResult peakIntervalCal(const std::vector<int>& peakIndexList, std::pair<int, int> validRange)
    {
        PeakIntervalResult result = {0, 0, {}};
        if (peakIndexList.size() < 2)
            return result;
        std::vector<int> peakIndexArr;
        for (const auto& index : peakIndexList) {
            if (index >= validRange.first && index <= validRange.second)
                peakIndexArr.push_back(index);
        }
        for (size_t i = 1; i < peakIndexArr.size(); ++i) {
            int interval = peakIndexArr[i] - peakIndexArr[i - 1];
            result.validIntervalList.push_back(interval);
            result.intervalSum += interval;
        }
        result.intervalNum = result.validIntervalList.size();
        return result;
    }

    /// 峰值数量校正（补峰与除峰）
    /// \param peakIndexList 峰值索引序列
    /// \param refInterval 峰值间隔参考值（用于进行峰值检验）
    /// \param sigmaL 有效间隔判定左邻域（峰值点参考间隔的倍数，0~1，默认0.3）
    /// \param sigmaR 有效间隔判定右邻域（峰值点参考间隔的倍数，0~1，默认0.5）
    /// \return 校正后的峰值索引序列
    std::vector<int> peakNumAdjust(const std::vector<int>& peakIndexList, int refInterval, float sigmaL, float sigmaR)
    {
        if (peakIndexList.size() < 2)
            return {};
        std::vector<int> peakIndexArr = peakIndexList;
        std::vector<int> intervalList(peakIndexArr.size() - 1);
        for(int i = 1; i < peakIndexArr.size(); i++)
            intervalList[i - 1] = peakIndexArr[i] - peakIndexArr[i - 1];
        
        if (refInterval == -1)
            refInterval = std::accumulate(intervalList.begin(), intervalList.end(), 0) / intervalList.size();
        int peakIntervalTmp = 0;
        int peakIndexInd = 0;
        for (size_t i = 0; i < intervalList.size(); ++i) {
            int curPeakInterval = intervalList[i] + peakIntervalTmp;
            if (curPeakInterval > refInterval * (1 + sigmaR)) {
                int appendPeakIndex = (peakIndexArr[peakIndexInd] + peakIndexArr[peakIndexInd + 1]) / 2;
                peakIndexArr.insert(peakIndexArr.begin() + peakIndexInd + 1, appendPeakIndex);
                peakIndexInd += 2;
                peakIntervalTmp = 0;
            } else if (curPeakInterval < refInterval * (1 - sigmaL)) {
                peakIndexArr.erase(peakIndexArr.begin() + peakIndexInd + 1);
                peakIntervalTmp += intervalList[i];
            } else {
                peakIndexInd++;
                peakIntervalTmp = 0;
            }
        }
        return peakIndexArr;
    }

    /// 峰值位置校正（最近邻或最大值）
    /// \param wave 波形（在该波形中搜寻最佳的峰值点位置）
    /// \param potentialPeakIndexList 潜在峰值点索引序列（校正前的峰值点索引）
    /// \param thr 峰值后验阈值
    /// \param strategy 校正策略（最近邻峰值nn/邻域内最大峰值mp）
    /// \param sigma 邻域大小（峰值点平均间隔的倍数，默认0.2）
    /// \return 校正后的峰值点索引
    std::vector<int> peakPosAdjust(const std::vector<double>& wave, const std::vector<int>& potentialPeakIndexList,
                                   double thr, const std::string& strategy,
                                   float sigma)
    {
        if (potentialPeakIndexList.size() < 2)
            return {};
        std::vector<int> potentialPeakIndexArr = potentialPeakIndexList;
        std::vector<int> intervalList(potentialPeakIndexArr.size() - 1);
        for(int i = 1; i < potentialPeakIndexArr.size(); i++)
            intervalList[i - 1] = potentialPeakIndexArr[i] - potentialPeakIndexArr[i - 1];
        double intervalAvg = std::accumulate(intervalList.begin(), intervalList.end(), 0.0) / intervalList.size();
        std::vector<int> maximumIndexList = peakDetect(wave);
        std::vector<int> adjustPeakIndexList;
        for (const auto& peakIndex : potentialPeakIndexArr) {
            if (strategy == "nn") {
                int minIndex = std::distance(maximumIndexList.begin(), std::min_element(maximumIndexList.begin(), maximumIndexList.end(), [peakIndex](int a, int b) {
                    return std::abs(a - peakIndex) < std::abs(b - peakIndex);
                }));
                if (std::abs(maximumIndexList[minIndex] - peakIndex) > intervalAvg * sigma)
                    adjustPeakIndexList.push_back(peakIndex);
                else
                    adjustPeakIndexList.push_back(maximumIndexList[minIndex]);
            } else if (strategy == "mp") {
                std::vector<int> neighborPeakIndexList;
                for (const auto& index : maximumIndexList) {
                    if (std::abs(index - peakIndex) <= intervalAvg * sigma)
                        neighborPeakIndexList.push_back(index);
                }
                if (!neighborPeakIndexList.empty()) {
                    int maxIndex = std::distance(neighborPeakIndexList.begin(), std::max_element(neighborPeakIndexList.begin(), neighborPeakIndexList.end(), [wave](int a, int b) {
                        return wave[a] < wave[b];
                    }));
                    adjustPeakIndexList.push_back(neighborPeakIndexList[maxIndex]);
                } else {
                    adjustPeakIndexList.push_back(peakIndex);
                }
            } else {
                throw std::invalid_argument("Undefined strategy name!");
            }
            if (wave[adjustPeakIndexList.back()] < thr)
                adjustPeakIndexList.pop_back();
        }
        return adjustPeakIndexList;
    }

    /// 伪峰剔除
    /// \param wave 波形（在该波形中峰值点）
    /// \param peakIndexList 峰值点索引序列
    /// \param reversePeakIndexList 反向峰值点索引序列（用于计算峰值点的高度）
    /// \param rateThr 比例阈值（伪峰与相邻峰值差值的判断阈值，相邻峰值的倍数）
    /// \param biDirCheck 是否双向校验（峰值高度是否需要同时满足左右邻）
    /// \return 校正后的峰值点索引
    std::vector<int> fakePeakDel(const std::vector<double>& wave, const std::vector<int>& peakIndexList,
                                 const std::vector<int>& reversePeakIndexList, float rateThr,
                                 bool biDirCheck)
    {
        if (peakIndexList.size() < 2 || reversePeakIndexList.size() < 2)
            return peakIndexList;
        std::vector<int> realPeakIndexList = {peakIndexList[0]};
        std::vector<double> peakAmpList;
        for (const auto& peakIndex : peakIndexList) {
            auto nearestReversePeakIndex = *std::min_element(reversePeakIndexList.begin(), reversePeakIndexList.end(),
                                                             [&peakIndex](int a, int b) { return std::abs(a - peakIndex) < std::abs(b - peakIndex); });
            double peakAmp = wave[peakIndex] - wave[nearestReversePeakIndex];
            peakAmpList.push_back(peakAmp);
        }
        if (biDirCheck) {
            for (size_t i = 1; i < peakIndexList.size() - 1; ++i) {
                if (peakAmpList[i] > rateThr * peakAmpList[i-1] && peakAmpList[i] > rateThr * peakAmpList[i+1])
                    realPeakIndexList.push_back(peakIndexList[i]);
            }
        } else {
            for (size_t i = 1; i < peakIndexList.size() - 1; ++i) {
                if (peakAmpList[i] > rateThr * peakAmpList[i-1] || peakAmpList[i] > rateThr * peakAmpList[i+1])
                    realPeakIndexList.push_back(peakIndexList[i]);
            }
        }
        realPeakIndexList.push_back(peakIndexList.back());
        return realPeakIndexList;
    }
}