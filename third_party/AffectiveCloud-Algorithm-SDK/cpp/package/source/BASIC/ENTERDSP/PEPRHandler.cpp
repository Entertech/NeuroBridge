//
// Created by Enter M1 on 2023/8/29.
//

#include "PEPRHandler.h"
#include "Pretreat.h"
#include "Basic.hpp"
#include "Common.h"
#include "DSPBCG.h"
#include "DSPPEPR.h"
#include "DSPHR.h"
#include "MathTool.h"
#include <numeric>
#include <algorithm>
#include <cmath>
#include <unordered_set>
#include "NumCpp.hpp"

namespace basic::dsp::peprhandler {
    void initTemp(PEPRHandlerTemp &tmp) {
        tmp.peDataBuffer.clear();
        tmp.preHr = 0.;
        tmp.preHrv = 0.;
        tmp.nnIntervalBuffer.clear();
        tmp.hrBuffer.clear();
        tmp.preBcgBoundary.assign(25, 0.);
        tmp.bcgAmpBuffer.clear();
        tmp.peRangeBuffer.clear();
        tmp.bcgQualityBuffer.clear();
        tmp.preBcgFl = 0.8;
        tmp.preBcgFh = 1.8;

        tmp.peDataBufferLong.clear();
        tmp.preRr = 0.;
        tmp.preRwLfBoundary.assign(5, 0.);
        tmp.rwHfDrift = -1;
        tmp.preInputRwHfFilterData.clear();
        tmp.preOutputRwHfFilterData.clear();
        tmp.preInputRwFilterData.clear();
        tmp.preOutputRwFilterData.clear();
        tmp.intervalDataDrift = 0.;
        tmp.preSyncCor = 0.;
        tmp.rwCalWaveBuffer.clear();
        tmp.rwHfRate = 1.;
        tmp.rwRangeBuffer.clear();

        tmp.peakIndexShift = 0;
        tmp.prePeakIndexList.clear();
        tmp.prePeakIndexRes.clear();
        tmp.preNnIntervalAvg = 0.;
        tmp.preNnInterval = 0.;
        tmp.nnIntervalStatBuffer.clear();
    }

    /// \brief 压电压阻算法处理器
    /// \param peRawData 原始压电数据
    /// \param prRawData 原始压阻数据
    /// \param bcgSplitSec 脉搏波片段长度（单位：秒）
    /// \param rwSplitSec 呼吸波片段长度（单位：秒）
    /// \param bcgEpochSec 用于计算心率等参数的脉搏波波形部分长度（单位：秒）
    /// \param rwEpochSec 用于计算心率等参数的呼吸波波形部分长度（单位：秒）
    /// \param bcgInvalidSec 脉搏波尾部无效长度（避免滤波引起的尾部振荡导致结果不准，单位：秒）
    /// \param rwInvalidSec 呼吸波尾部无效长度（避免滤波引起的尾部振荡导致结果不准，单位：秒）
    /// \param deviceInfo 设备信息
    /// \param tmp 缓存
    /// \param scopeLim 计算心率变异性及频段能量所需的心率序列时长（单位：秒）
    /// \param rwHfRate 呼吸波快波比例（调节该值来平衡呼吸波快波与慢波的比例，None表示自适应）
    /// \param peSampleInterval 压电信号降采样使用的采样间隔
    /// \return 处理后的信号及其他结果，更新后的缓存
    PEPRHandlerResult handler(const vectori &peRawData, const vectori &prRawData, double bcgSplitSec, double rwSplitSec,
                              double bcgEpochSec, double rwEpochSec, double bcgInvalidSec, double rwInvalidSec,
                              DeviceInfo *deviceInfo, PEPRHandlerTemp &tmp, double scopeLim, double rwHfRate,
                              int peSampleInterval) {
        // 初始化
        PEPRHandlerResult res;
        res.hr = 0.;
        res.hrv = 0.;
        res.rr = 0.;
        res.bcgQuality = BCGQuality::BCG_NONE;
        res.rwQuality = RWQuality::RW_NONE;
        res.syncCor = 0.;
        res.nnIntervalFeatureAmo = 0.;
        res.nnIntervalFeatureMo = 0.;
        res.nnIntervalFeatureMxdmn = 0.;
        auto bcgDsFs = deviceInfo->peFs() / peSampleInterval; // 降采样后的脉搏波采样率
        int peStepLen = (int) peRawData.size();
        auto prStepLen = (int) prRawData.size();
        auto bcgSplitLen = (int) (bcgSplitSec * bcgDsFs);                // 脉搏波信号片段长度
        auto bcgEpochLen = (int) (bcgEpochSec * bcgDsFs);                // 用于计算脉搏波参数的信号长度
        auto bcgInvalidLen = (int) (bcgInvalidSec * bcgDsFs);            // 脉搏波尾部无效的压电信号长度
        auto bcgEpochStart = bcgSplitLen - bcgInvalidLen - bcgEpochLen; // 用于计算脉搏波参数的压电信号起始点
        auto bcgEpochEnd = bcgSplitLen - bcgInvalidLen;                 // 用于计算脉搏波参数的压电信号结束点
        auto rwSplitLen = (int) (rwSplitSec * deviceInfo->prFs());       // 呼吸波信号片段长度
        auto rwEpochLen = (int) (rwEpochSec * deviceInfo->prFs());       // 用于计算呼吸波参数的信号长度
        auto rwInvalidLen = (int) (rwInvalidSec * deviceInfo->prFs());   // 呼吸波尾部无效的压电信号长度
        auto rwEpochStart = rwSplitLen - rwInvalidLen - rwEpochLen;     // 用于计算呼吸波参数的压电信号起始点
        auto rwEpochEnd = rwSplitLen - rwInvalidLen;                    // 用于计算呼吸波参数的压电信号结束点
        auto stepSec = peStepLen / deviceInfo->peFs();                  // 步长

        // 数据预处理
        // ---有效性校验
        if (stepSec > bcgEpochSec)
            return res;

        // ---负载判断
        if (!peprLoadCheck(peRawData)) {
            int peakIndexShiftTmp = 0;
            // 保存缓存内容
            if (tmp.peDataBuffer.size() < bcgSplitLen)
                peakIndexShiftTmp = tmp.peakIndexShift + (int) tmp.peDataBuffer.size() + peStepLen;
            else
                peakIndexShiftTmp = tmp.peakIndexShift + (int) tmp.peDataBuffer.size();
            vectori prePeakIndexListTmp(tmp.prePeakIndexList.begin(), tmp.prePeakIndexList.end());

            // 重新初始化
            initTemp(tmp);
            // 输出结果
            res.bcgWave.assign(peStepLen, 0.);
            res.rwWave.assign(prStepLen, 0.);
            // 缓存恢复
            tmp.peakIndexShift = peakIndexShiftTmp;
            tmp.prePeakIndexList.assign(prePeakIndexListTmp.begin(), prePeakIndexListTmp.end());
            return res;
        }

        // ---降采样
        vectord peSampleData;
        for (size_t i = 0; i < peRawData.size(); i += 5) {
            peSampleData.push_back((double) peRawData[i]);
        }

        // ---电压计算
        auto peStepData = voltageCal(peSampleData,
                                     deviceInfo->peMaxMv(), deviceInfo->peMinMv(),
                                     deviceInfo->peMaxVal(), deviceInfo->peMinVal());

        // ---数据拼接
        tmp.peDataBuffer.insert(tmp.peDataBuffer.end(), peStepData.begin(), peStepData.end());
        if (tmp.peDataBuffer.size() < bcgSplitLen) {
            res.bcgWave.assign(peStepLen, 0.);
            res.rwWave.assign(prStepLen, 0.);
            return res;
        }
        tmp.peDataBuffer.assign(tmp.peDataBuffer.end() - bcgSplitLen, tmp.peDataBuffer.end());

        tmp.peDataBufferLong.insert(tmp.peDataBufferLong.end(), peStepData.begin(), peStepData.end());
        auto peDataBufferLongLen = (int) (rwSplitSec * bcgDsFs);
        if (tmp.peDataBufferLong.size() > peDataBufferLongLen) {
            tmp.peDataBufferLong.assign(tmp.peDataBufferLong.end() - peDataBufferLongLen, tmp.peDataBufferLong.end());

        }

        // 自适应滤波参数
        double fl = 0.8;
        double fh = 1.8;
        if (tmp.preHr != 0) {
            auto bcgQualityBufferSum = mathtool::sum(tmp.bcgQualityBuffer);
            auto bcgNORMQualitySum = BCGQuality::BCG_NORM * (int) (25 / stepSec);
            if (bcgQualityBufferSum >= bcgNORMQualitySum) {
                fl = 0.8 > (tmp.preHr - 18) / 60 ? 0.8 : (tmp.preHr - 18) / 60;
                fh = 1.8 < (tmp.preHr + 18) / 60 ? 1.8 : (tmp.preHr + 18) / 60;
            }
            else
            {
                fl = tmp.preBcgFl;
                fh = tmp.preBcgFh;
            }
        }

        // 脉搏波信号处理
        // ---直流去除
        double peDataBufferMean =
                std::accumulate(tmp.peDataBuffer.begin(), tmp.peDataBuffer.end(), 0.0) / tmp.peDataBuffer.size();

        std::vector<double> noDriftWave(tmp.peDataBuffer);
        std::transform(noDriftWave.begin(), noDriftWave.end(), noDriftWave.begin(),
                       [peDataBufferMean](double x) { return x - peDataBufferMean; });

        // --- 带通滤波
        auto filterWave = bandpassFilter(noDriftWave, 0.8, 10.0, bcgDsFs);

        // --- 差分平方和计算
        auto diffM1Wave = diffMed1Cal(filterWave, 1);
        auto diffM2Wave = diffMed2Cal(filterWave, 1);
        std::vector<double> diffSquareSum(diffM1Wave.size());

        std::transform(diffM1Wave.begin(), diffM1Wave.end(), diffM2Wave.begin(), diffSquareSum.begin(),
                       [](double a, double b) { return 2 * std::sqrt(std::pow(a, 2) + std::pow(b, 2)); });
        // --- 带通滤波
        auto bcgFilterWave = bandpassFilter(filterWave, fl, fh, bcgDsFs);
        // ---输出波形
        std::vector<double> bcgWave(bcgFilterWave);

        auto tAxis = nc::linspace(0.,(double)bcgFilterWave.size(),bcgFilterWave.size()*peSampleInterval);
        auto tAxisSample = nc::linspace(0.,(double)bcgFilterWave.size(),bcgFilterWave.size());
        auto bcgOutputWaveNc = nc::interp(tAxis,tAxisSample,nc::NdArray<double>(bcgFilterWave));
        auto bcgOutputWave = bcgOutputWaveNc.toStlVector();

        // 心率特征计算
        //  ---峰值检测
        //  Find peaks
        auto potentialPeakIndexList = peakDetect(bcgWave);
        // Find peaks in the negated signal
        std::vector<double> negatedBcgWave(bcgWave.size());
        std::transform(bcgWave.begin(), bcgWave.end(), negatedBcgWave.begin(), std::negate<double>());
        auto reversePeakIndexList = peakDetect(negatedBcgWave);

        // --- 伪峰剔除
        auto realPeakIndexList = fakePeakDel(bcgWave, potentialPeakIndexList,
                                             reversePeakIndexList, 0.2f, false);

        // ---峰值个数校正
        int refInterval = -1;
        if (tmp.preHr != 0)
            refInterval = (int) round(60 / tmp.preHr * bcgDsFs);

        auto adjustPeakIndexList = peakNumAdjust(realPeakIndexList, refInterval, 0.4f, 0.6f);

        // ---峰值位置校正
        auto peakIndexList = peakPosAdjust(diffSquareSum, adjustPeakIndexList,
                                           0., "nn", 0.2f);

        // ---峰值间隔计算
        auto peakIntervalResult = peakIntervalCal(peakIndexList, {bcgEpochStart, bcgEpochEnd});

        // ---峰值索引计算
        // Filter the peak index list
        std::vector<int> validIndexList;
        for (auto peakIndex: peakIndexList) {
            if (peakIndex < bcgEpochEnd) {
                validIndexList.push_back(peakIndex);
            }
        }
        // Add the shift
        for (auto &peakIndex: validIndexList) {
            peakIndex += tmp.peakIndexShift;
        }

        std::vector<int> peakIndexRes;
        for (auto peakIndex: validIndexList) {
            double refIndexInterval = refInterval != -1 ? refInterval * 0.5 : 50.;
            if (!tmp.prePeakIndexList.empty()) {
                vectori peakIndexTmp;
                for (auto &e: tmp.prePeakIndexList) {
                    peakIndexTmp.push_back(abs(peakIndex - e));
                }
                auto minAbs = mathtool::min(peakIndexTmp);
                int maxIndex = *std::max_element(tmp.prePeakIndexList.begin(), tmp.prePeakIndexList.end());
                if ((double) minAbs > refIndexInterval && peakIndex > maxIndex) {
                    peakIndexRes.push_back(peakIndex);
                }
            } else {
                peakIndexRes.push_back(peakIndex);
            }
        }

        // 信号质量检测
        // Calculate bcg amplitude
        double bcgAmp = 0.; //*std::max_element(bcgWave.begin() + bcgEpochStart, bcgWave.begin() + bcgEpochEnd);
        for (size_t i = bcgEpochStart; i < bcgEpochEnd; ++i) {
            auto absValue = abs(bcgWave[i]);
            if (absValue > bcgAmp)
                bcgAmp = absValue;
        }
        tmp.bcgAmpBuffer.push_back(bcgAmp);
        if (tmp.bcgAmpBuffer.size() > static_cast<int>(15 / stepSec)) {
            tmp.bcgAmpBuffer.assign(tmp.bcgAmpBuffer.end() - static_cast<int>(15 / stepSec), tmp.bcgAmpBuffer.end());
        }
        double bcgAmpThr = std::accumulate(tmp.bcgAmpBuffer.begin(), tmp.bcgAmpBuffer.end(), 0.0) / tmp.bcgAmpBuffer.size() * 2;
        // Calculate pe range
        double peRange = *std::max_element(tmp.peDataBuffer.begin(), tmp.peDataBuffer.end()) -
                         *std::min_element(tmp.peDataBuffer.begin(), tmp.peDataBuffer.end());
        tmp.peRangeBuffer.push_back(peRange);
        if (tmp.peRangeBuffer.size() > static_cast<int>(15 / stepSec)) {
            tmp.peRangeBuffer.assign(tmp.peRangeBuffer.end() - static_cast<int>(15 / stepSec), tmp.peRangeBuffer.end());
        }
        double peRangeThr = std::accumulate(tmp.peRangeBuffer.begin(), tmp.peRangeBuffer.end(), 0.0) / tmp.peRangeBuffer.size() * 2;

        auto bcgQuality = bcgQualityCal(std::vector<double>(bcgWave.begin() + bcgEpochStart,
                                                            bcgWave.begin() + bcgEpochEnd),
                                        tmp.peDataBuffer,
                                        bcgAmpThr, peRangeThr);
        // 心率计算
        double hr = 0.;
        if (bcgQuality == BCGQuality::BCG_NORM) {
            if (peakIntervalResult.intervalNum == 0 || peakIntervalResult.intervalSum == 0)
                hr = tmp.preHr;
            else {
                auto curHr = 60.0 * peakIntervalResult.intervalNum / peakIntervalResult.intervalSum * bcgDsFs;
                if (tmp.preHr == 0)
                    hr = curHr;
                else
                    hr = mathtool::smoothAvg(curHr, tmp.preHr, 1.6 / (1 + stepSec / 0.6));
            }
        } else {
            hr = tmp.preHr;
        }

        // 心率间期计算
        double preNnIntervalAvg = tmp.preNnIntervalAvg; // Assuming tmp.preNnIntervalAvg is already defined
        double preNnInterval = tmp.preNnInterval;       // Assuming tmp.preNnInterval is already defined
        double nnIntervalAvg;
        std::vector<double> nnIntervalSeq;
        if (bcgQuality == BCGQuality::BCG_NORM) {
            if (peakIntervalResult.intervalNum == 0 || peakIntervalResult.intervalSum == 0) {
                nnIntervalAvg = preNnIntervalAvg;
            } else {
                double curNnIntervalAvg =
                        1000.0 * (double)peakIntervalResult.intervalSum / (double)peakIntervalResult.intervalNum / bcgDsFs;
                if (preNnIntervalAvg == 0) {
                    nnIntervalAvg = curNnIntervalAvg;
                } else {
                    nnIntervalAvg = mathtool::smoothAvg(curNnIntervalAvg, preNnIntervalAvg, 1.4 / (1 + stepSec / 0.6));
                }
            }
            preNnInterval = tmp.preNnInterval;
            for (size_t i = 0; i < peakIndexRes.size(); ++i) {
                double curInterval;
                if (i == 0) {
                    if (!tmp.prePeakIndexList.empty()) {
                        curInterval = peakIndexRes[i] - tmp.prePeakIndexList.back();
                    } else {
                        curInterval = 0;
                    }
                } else {
                    curInterval = peakIndexRes[i] - peakIndexRes[i - 1];
                }
                double curNnInterval = curInterval / bcgDsFs * 1000;
                if (preNnInterval > 0.) {
                    curNnInterval = mathtool::smoothAvg(curNnInterval, preNnInterval, 1.6 / (1 + stepSec / 0.6));
                }
                preNnInterval = curNnInterval;
                nnIntervalSeq.push_back(curNnInterval);
            }
        } else {
            nnIntervalAvg = preNnIntervalAvg;
        }

        // 心率变异性计算
        auto hrv = tmp.preHrv; // Assuming tmp.preHrv is already defined
        if (bcgQuality == BCGQuality::BCG_NORM) {
            if (nnIntervalAvg > 0) {
                tmp.nnIntervalBuffer.push_back(nnIntervalAvg);
            }
            if ((double)tmp.nnIntervalBuffer.size() * stepSec >= scopeLim) {

                tmp.nnIntervalBuffer.assign(
                        tmp.nnIntervalBuffer.end() - static_cast<int>(std::ceil(scopeLim / stepSec)),
                        tmp.nnIntervalBuffer.end());


                int nnIntervalInvalidLen = static_cast<int>(std::ceil(scopeLim * 0.02 / stepSec));
                vectord nnIntervalBufferTmp(tmp.nnIntervalBuffer.begin(), tmp.nnIntervalBuffer.end());
                std::sort(nnIntervalBufferTmp.begin(), nnIntervalBufferTmp.end());
                std::vector<double> validNnIntervalBuffer(nnIntervalBufferTmp.begin() + nnIntervalInvalidLen,
                                                          nnIntervalBufferTmp.end() - nnIntervalInvalidLen);
                double curHrv = 0.0;
                double sum = std::accumulate(validNnIntervalBuffer.begin(), validNnIntervalBuffer.end(), 0.0);
                double mean = sum / validNnIntervalBuffer.size();

                double sqSum = std::inner_product(validNnIntervalBuffer.begin(), validNnIntervalBuffer.end(), validNnIntervalBuffer.begin(), 0.0);
                double stdDeviation = std::sqrt(sqSum / validNnIntervalBuffer.size() - mean * mean);

                if (validNnIntervalBuffer.size() > 1)
                    curHrv = stdDeviation * std::sqrt(validNnIntervalBuffer.size() / (validNnIntervalBuffer.size() - 1.0));
                if (tmp.preHrv < 0 || tmp.preHr > 200) {
                    hrv = curHrv;
                } else {
                    hrv = mathtool::smoothAvg(curHrv, tmp.preHrv, 0.7); // Assuming smoothAvg is already defined
                }
            } else {
                hrv = 0;
            }
        }

        // 利用压电漂移特征合成呼吸波（快波）
        // ---漂移去除
        if (tmp.rwHfDrift == -1.0)
            tmp.rwHfDrift = peStepData[0];
        std::vector<double> rwHfStepData(peStepData.size());
        auto rwHfDrift = tmp.rwHfDrift;
        std::transform(peStepData.begin(), peStepData.end(), rwHfStepData.begin(),
                       [rwHfDrift](double n) { return n - rwHfDrift; });

        // ---带通滤波
        if (tmp.preInputRwHfFilterData.empty())
            tmp.preInputRwHfFilterData.assign(rwHfStepData.size(), 0.);
        if (tmp.preOutputRwHfFilterData.empty())
            tmp.preOutputRwHfFilterData.assign(rwHfStepData.size(), 0.);
        auto rwHfStepWave = rwHfStepData;

        // 利用脉搏波特征合成呼吸波（慢波）
        vectord rwLfStepWave(peStepData.size(), 0.);
        if (nnIntervalAvg > 0.) {
            // ---脉搏波间隔特征
            vectord intervalData(peStepData.size(), nnIntervalAvg);
            if (tmp.intervalDataDrift == 0.)
                tmp.intervalDataDrift = nnIntervalAvg;
            auto intervalDataDrift = mathtool::smoothAvg(nnIntervalAvg, tmp.intervalDataDrift, 0.9);
            tmp.intervalDataDrift = intervalDataDrift;
            // ---脉搏波呼吸特征数据合成
            std::vector<double> rwLfStepData(intervalData.size());
            std::transform(intervalData.begin(), intervalData.end(), rwLfStepData.begin(),
                           [intervalDataDrift](double n) { return -(n - intervalDataDrift) * 0.8; });
            // ---边界处理
            auto rwLfStepWavePair = boundaryComp(rwLfStepData, tmp.preRwLfBoundary, 5);
            rwLfStepWave.assign(rwLfStepWavePair.first.begin(), rwLfStepWavePair.first.end());
            tmp.preRwLfBoundary.assign(rwLfStepWavePair.second.begin(), rwLfStepWavePair.second.end());
        }

        // 呼吸波合成
        if (rwHfRate == -1.0) {
            if (tmp.preSyncCor < 0.4) {
                tmp.rwHfRate += 0.1;
                tmp.rwHfRate = std::min(tmp.rwHfRate, 1.0);
            } else if (tmp.preSyncCor > 0.6) {
                tmp.rwHfRate -= 0.1;
                tmp.rwHfRate = std::max(tmp.rwHfRate, 0.0);
            }
            rwHfRate = tmp.rwHfRate;
        }
        std::vector<double> rwStepWave(rwHfStepWave.size());
        for (size_t i = 0; i < rwHfStepWave.size(); ++i) {
            rwStepWave[i] = rwHfRate * rwHfStepWave[i] + (1 - rwHfRate) * rwLfStepWave[i];
        }

        // ---带通滤波
        if (tmp.preInputRwFilterData.empty())
            tmp.preInputRwFilterData.assign(rwHfStepData.size(), 0.);
        if (tmp.preOutputRwFilterData.empty())
            tmp.preOutputRwFilterData.assign(rwHfStepData.size(), 0.);
        auto rwOutputWave = digitalFilter(rwStepWave, tmp.preInputRwFilterData,
                                          tmp.preOutputRwFilterData, 0.07, 0.4, deviceInfo->prFs(), 4);
        tmp.preInputRwFilterData.assign(rwStepWave.begin(), rwStepWave.end());
        tmp.preOutputRwFilterData.assign(rwOutputWave.begin(), rwOutputWave.end());

        // 呼吸特征计算
        //  ---缓存
        for (auto item: rwOutputWave) {
            tmp.rwCalWaveBuffer.push_back(item);
        }
        if (tmp.rwCalWaveBuffer.size() > rwSplitLen) {
            tmp.rwCalWaveBuffer.assign(tmp.rwCalWaveBuffer.end() - rwSplitLen, tmp.rwCalWaveBuffer.end());
        }
        //---峰值检测
        auto rwPotentialPeakIndexList = peakDetect(tmp.rwCalWaveBuffer);
        vectord rwCalWaveBufferTmp(tmp.rwCalWaveBuffer.begin(), tmp.rwCalWaveBuffer.end());
        for (auto &e: rwCalWaveBufferTmp) {
            e = -e;
        }
        auto rwReversePeakIndexList = peakDetect(rwCalWaveBufferTmp);
        // ---伪峰剔除
        auto rwRealPeakIndexList = fakePeakDel(tmp.rwCalWaveBuffer, rwPotentialPeakIndexList,
                                               rwReversePeakIndexList, 0.5f, true);
        // ---峰值位置校正
        auto rwAdjustPeakIndexList = peakPosAdjust(tmp.rwCalWaveBuffer, rwRealPeakIndexList,
                                                   0., "nn", 0.2f);
        // ---峰值个数校正
        auto rwPeakIndexList = peakNumAdjust(rwAdjustPeakIndexList, -1, 0.5f, 0.5f);
        // ---峰值间隔计算
        auto rwPeakIntervalResult = peakIntervalCal(rwPeakIndexList, {rwEpochStart, rwEpochEnd});
        auto rwIntervalNum = rwPeakIntervalResult.intervalNum;
        auto rwIntervalSum = rwPeakIntervalResult.intervalSum;
        auto rwIntervalList = rwPeakIntervalResult.validIntervalList;

        // 信号质量检测
        auto rwQuality = RWQuality::RW_NONE;
        if (tmp.rwCalWaveBuffer.size() >= rwSplitLen) {
            auto peDataBufferLongMax = *std::max_element(tmp.peDataBufferLong.begin(), tmp.peDataBufferLong.end());
            auto peDataBufferLongMin = *std::min_element(tmp.peDataBufferLong.begin(), tmp.peDataBufferLong.end());
            auto rwRange = peDataBufferLongMax - peDataBufferLongMin;
            tmp.rwRangeBuffer.push_back(rwRange);
            auto stepSecLen = static_cast<int>(15 / stepSec);
            if (tmp.rwRangeBuffer.size() > stepSecLen) {
                tmp.rwRangeBuffer.assign(tmp.rwRangeBuffer.end() - stepSecLen, tmp.rwRangeBuffer.end());
            }
            auto rwRangeThr = mathtool::mean(tmp.rwRangeBuffer) * 3;
            vectord rwCalWaveBufferRangeTmp(tmp.rwCalWaveBuffer.begin() + rwEpochStart,
                                            tmp.rwCalWaveBuffer.begin() + rwEpochEnd);
            rwQuality = rwQualityCal(rwCalWaveBufferRangeTmp, tmp.peDataBufferLong, rwRangeThr);
        }

        // 呼吸率计算
        double rr = tmp.preRr;
        if (rwQuality == RWQuality::RW_NORM) {
            if (rwIntervalNum == 0 || rwIntervalSum == 0)
                rr = tmp.preRr;
            else {
                auto curRr = 60.0 / rwIntervalList.back() * deviceInfo->prFs();
                if (tmp.preRr == 0)
                    rr = curRr;
                else
                    rr = mathtool::smoothAvg(curRr, tmp.preRr, 0.96);
            }
        }



        // 频段能量计算
        HRPower hrPower;
        auto hrFs = 1 / stepSec;
        if (tmp.nnIntervalBuffer.size() * stepSec >= scopeLim && hrFs > 0.4) {
            hrPower.power = hrvPowerCal(tmp.nnIntervalBuffer, 0, 0.4, hrFs);
            hrPower.hf = hrvPowerCal(tmp.nnIntervalBuffer, 0.15, 0.4, hrFs);
            hrPower.lf = hrvPowerCal(tmp.nnIntervalBuffer, 0.04, 0.15, hrFs);
            hrPower.vlf = hrvPowerCal(tmp.nnIntervalBuffer, 0.003, 0.04, hrFs);
        }

        // 神经系统同步频率能量计算
        if (hr > 0)
            tmp.hrBuffer.push_back(hr);
        if (tmp.hrBuffer.size() > static_cast<int>(26 / stepSec)) {
            tmp.hrBuffer.assign(tmp.hrBuffer.end() - static_cast<int>(26 / stepSec), tmp.hrBuffer.end());
        }

        double syncCor = 0.;
        if (tmp.hrBuffer.size() >= static_cast<int>(26 / stepSec)) {
            // 神经系统同步频率能量
            auto hrWaveMean = mathtool::mean(tmp.hrBuffer);
            auto hrWave = tmp.hrBuffer;
            for (auto &e: hrWave) {
                e -= hrWaveMean;
            }
            std::unordered_set<double> uniqueElements(tmp.hrBuffer.begin(), tmp.hrBuffer.end());
            if (uniqueElements.size() > 1) {
                vectord corList;
                vectord corParams = {0.07, 0.08, 0.09, 0.1, 0.11, 0.12}; // 遍历神经系统同步频率，计算相关系数最大值
                nc::NdArray<double> hrWaveNd(hrWave, false);
                for (auto corParam: corParams) {
                    auto cor = waveFreqCor(hrWaveNd, corParam, hrFs);
                    corList.push_back(cor);
                }
                auto hrRange = mathtool::max(tmp.hrBuffer) - mathtool::min(tmp.hrBuffer);
                auto syncCorFactor = hrRange / 5.0 > 1.0 ? 1.0 : hrRange / 5.0;
                syncCor = mathtool::max(corList) * syncCorFactor;
            }
        }

        // 输出
        // ---输出信号质量
        res.bcgQuality = bcgQuality;
        res.rwQuality = rwQuality;
        // ---输出波形
        auto bcgOutputStart = (int) ((bcgEpochStart + bcgEpochEnd) / 2.0 * peSampleInterval);
        // get bcgOutputWave from bcgOutputStart to bcgOutputStart+peStepLen
        vectord bcgOutputWaveSplit = {bcgOutputWave.begin() + bcgOutputStart,
                                      bcgOutputWave.begin() + bcgOutputStart + peStepLen};
        auto boundaryCompRes = boundaryComp(bcgOutputWaveSplit, tmp.preBcgBoundary, 25);
        bcgOutputWave.clear();
        res.bcgWave.assign(boundaryCompRes.first.begin(), boundaryCompRes.first.end());
        auto curBcgBoundary = boundaryCompRes.second;
        res.rwWave = rwOutputWave;
        // ---输出心跳间隔
        if (bcgQuality == BCGQuality::BCG_NORM) {
            res.nnInterval.assign(nnIntervalSeq.begin(), nnIntervalSeq.end());
        } else {
            res.nnInterval.clear();
        }
        // ---输出心率
        res.hr = hr;
        // ---输出心率变异性
        res.hrv = hrv;
        // ---输出呼吸波
        res.rr = rr;
        // ---输出频段能量
        res.hrPower = hrPower;
        // ---输出神经系统同步频率相关系数
        res.syncCor = syncCor;
        // ---输出峰值索引
        res.peakIndex = peakIndexRes;

        // 缓存更新
        tmp.preHr = res.hr;
        tmp.preHrv = res.hrv;
        tmp.preNnIntervalAvg = nnIntervalAvg;
        tmp.preRr = res.rr;
        tmp.preBcgBoundary.assign(curBcgBoundary.begin(), curBcgBoundary.end());
        tmp.preSyncCor = res.syncCor;
        tmp.peakIndexShift += peStepLen;
        tmp.prePeakIndexList.assign(validIndexList.begin(), validIndexList.end());
        tmp.prePeakIndexRes = peakIndexRes;
        if (nnIntervalSeq.size() > 0)
            tmp.preNnInterval = nnIntervalSeq.back();
        tmp.bcgQualityBuffer.push_back(bcgQuality);
        if (tmp.bcgQualityBuffer.size() > static_cast<int>(25 / stepSec)) {
            tmp.bcgQualityBuffer.assign(tmp.bcgQualityBuffer.end() - static_cast<int>(25 / stepSec),
                                        tmp.bcgQualityBuffer.end());
        }
        if (bcgQuality == BCGQuality::BCG_NORM) {
            tmp.preBcgFh = fh;
            tmp.preBcgFl = fl;
        }

        return res;

    }
}