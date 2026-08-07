#include "Feature.h"
#include "Common.h"
#include "DSPEEG.h"
#include "Pretreat.h"
#include "NumCpp.hpp"
#include "DSPFrac.h"
#include "Basic.hpp"
#include "Wavelet.hpp"
#include <numeric>
namespace basic::dsp
{
    /// @brief 脑电频谱特征
    /// @param eegSplitData 脑电数据片段
    /// @param nfft FFT点数
    /// @param deviceInfo 设备信息
    /// @return 频谱幅值
    vectord generalSpectrum(const vectord &eegSplitData, int nfft, DeviceInfo *deviceInfo)
    {
        auto removeData = doubleOutlierRemove(eegSplitData);
        auto eegVoltData = voltageCal(removeData, deviceInfo->eegMaxUv(), deviceInfo->eegMinUv(), deviceInfo->eegMaxVal(), deviceInfo->eegMinVal());
        auto noDriftWave = eegDriftFilter(eegVoltData);
        auto nWave = eegArtifactRemove(noDriftWave);
        auto noArtifactWave = nc::NdArray<double>(nWave.first);
        auto feature = bandSpectrumCal(noArtifactWave, 0., 45., deviceInfo->eegFs(), false, nfft);
        return feature.toStlVector();
    }

    /// @brief 睡眠频段能量比例特征
    /// @param eegSplitData 脑电数据片段
    /// @param nfft FFT点数
    /// @param deviceInfo 设备信息
    /// @return 频谱幅值
    vectord sleepPowerRate(const vectord &eegSplitData, int nfft, DeviceInfo *deviceInfo)
    {
        auto eegSpec = generalSpectrum(eegSplitData, nfft, deviceInfo);
        auto ncSpec = nc::NdArray<double>(eegSpec);
        auto eegPowerSpec = nc::power(ncSpec, 2);

        auto sumPower = nc::sum(eegPowerSpec).item();
        auto eegSpindleRate = nc::sum(eegPowerSpec[nc::Slice(11, 16)]).item() / sumPower;
        auto eegGammaRate = nc::sum(eegPowerSpec[nc::Slice(30, eegPowerSpec.size())]).item() / sumPower;
        auto eegHighBetaRate = nc::sum(eegPowerSpec[nc::Slice(20, 30)]).item() / sumPower;
        auto eegBetaThetaRate = nc::sum(eegPowerSpec[nc::Slice(16, 30)]).item() / nc::sum(eegPowerSpec[nc::Slice(4, 8)]).item();
        vectord featureVector;
        featureVector.push_back(eegGammaRate);
        featureVector.push_back(eegHighBetaRate);
        featureVector.push_back(eegSpindleRate);
        featureVector.push_back(eegBetaThetaRate);
        nc::NdArray<double> feature(featureVector);

        auto featureTemp = nc::sqrt(feature);
        featureTemp[3] = nc::log(featureTemp[3]);
        return featureTemp.toStlVector();
    }

    /**
    * 睡眠脑电特征
    * @param eegSplitData 脑电数据片段
    * @param nfft FFT点数
    * @param deviceInfo 设备信息
    * @return
    */
    SleepEEGFeaturesCal sleepEEGFeatures(const vectord &eegSplitData, int nfft, DeviceInfo *deviceInfo)
    {
        //初始化
        auto specRate = static_cast<size_t>(nfft / deviceInfo->eegFs());

        // 信号处理
        auto eegSplitDataOutlier = doubleOutlierRemove(eegSplitData); //跳点剔除
        auto eegVoltData = voltageCal(eegSplitDataOutlier, deviceInfo->eegMaxUv(), deviceInfo->eegMinUv(), deviceInfo->eegMaxVal(), deviceInfo->eegMinVal()); // 电压计算
        eegSplitDataOutlier.clear();

        auto noDriftWave = eegDriftFilter(eegVoltData); // 漂移去除
        eegVoltData.clear();

        auto noPFWave = eegPfNotch(noDriftWave); // 工频陷波

        auto lowFilterWave = eegLowpassFilter(noPFWave); // 低通滤波

        auto filterWave = eegHighpassFilter(lowFilterWave); // 高通滤波
        lowFilterWave.clear();

        auto noArtifactWave = eegArtifactRemove(noPFWave); // 伪迹去除

        auto denoiseWave = eegWaveletDenoise(noArtifactWave.first, "db4", "soft"); // 硬阈值去噪
        noArtifactWave.first.clear();
        noArtifactWave.second.clear();

        vectord filterCutWave(filterWave.begin() + 90, filterWave.end() - 90); //波形截取

        //频域特征计算
        //---脑电节律能量
        auto eegPower = eegPowerCal(denoiseWave, deviceInfo);
        denoiseWave.clear();
        //---频谱计算
        nc::NdArray<double> filterCutWaveNc(filterCutWave);
        auto eegSpec = bandSpectrumCal(filterCutWaveNc, 0, 45, deviceInfo->eegFs(), true, nfft);
        auto eegPowerSpecNc = nc::power(eegSpec, 2);

        //---典型频段占比
        auto eegPowerSpec = eegPowerSpecNc.toStlVector();
        auto eegTotalPower = std::accumulate(eegPowerSpec.begin(), eegPowerSpec.end(), 0.0);//
        auto eegSpindlePower = std::accumulate(11*specRate+eegPowerSpec.begin(), 16*specRate+1+eegPowerSpec.begin(), 0.); //纺锤波能量
        auto eegTypicalSpindlePower = std::accumulate(12*specRate+eegPowerSpec.begin(), 14*specRate+1+eegPowerSpec.begin(), 0.); //典型纺锤波能量
        auto eegHighBetaPower = std::accumulate(20*specRate+eegPowerSpec.begin(), 30*specRate+1+eegPowerSpec.begin(), 0.);
        auto eegLowBetaPower = std::accumulate(16*specRate+eegPowerSpec.begin(), 20*specRate+1+eegPowerSpec.begin(), 0.);
        auto eegSpindleRate = eegSpindlePower / eegTotalPower;
        auto eegTypicalSpindleRate = eegTypicalSpindlePower / eegTotalPower;
        auto eegHighBetaRate = eegHighBetaPower / eegTotalPower;
        auto eegLowBetaRate = eegLowBetaPower / eegTotalPower;
        auto eegBetaThetaRate = eegPower.beta / eegPower.theta;
        auto alphaNorm = eegPower.alphaNorm();
        auto betaNorm = eegPower.betaNorm();
        auto thetaNorm = eegPower.thetaNorm();
        auto gammaNorm = eegPower.gammaNorm();
        auto deltaNorm = eegPower.deltaNorm();
        auto alphaDB = eegPower.alphaDB();
        auto betaDB = eegPower.betaDB();
        auto thetaDB = eegPower.thetaDB();
        auto gammaDB = eegPower.gammaDB();
        auto deltaDB = eegPower.deltaDB();
        auto powerDB = eegPower.powerDB();
        auto logHighBetaPower = 20 * log10(eegHighBetaPower);
        auto logLowBetaPower = 20 * log10(eegLowBetaPower);
        auto logEEGSpindlePower = 20 * log10(eegSpindlePower);
        auto logEEGTypicalSpindlePower = 20 * log10(eegTypicalSpindlePower);
        vectord featurePowerRate = {
                alphaNorm, betaNorm, thetaNorm, deltaNorm, gammaNorm,
                eegBetaThetaRate, eegHighBetaRate, eegLowBetaRate, eegSpindleRate, eegTypicalSpindleRate
        };

        vectord  featurePowerDB = {
                alphaDB, betaDB, thetaDB, deltaDB, gammaDB, powerDB,
                logHighBetaPower, logLowBetaPower, logEEGSpindlePower, logEEGTypicalSpindlePower
        };

        // 时域特征计算
        // ---包络特征
        auto envelope = fracEnvelopeCal(filterCutWave, 50);
        filterCutWave.clear();
        filterWave.clear();
        vectord envelopeAmp;
        for (auto e : envelope.first)
        {
            envelopeAmp.push_back(std::abs(e));
        }

        for (auto e : envelope.second)
        {
            envelopeAmp.push_back(std::abs(e));
        }

        vectord envelopeLowerDiff;
        for (int i = 0; i < envelope.first.size()-1; ++i) {
            envelopeLowerDiff.push_back(envelope.first[i+1]-envelope.first[i]);
        }
        vectord envelopeUpperDiff;
        for (int i = 0; i < envelope.second.size()-1; ++i) {
            envelopeUpperDiff.push_back(envelope.second[i+1]-envelope.second[i]);
        }
        vectord envelopeDiff;
        for (int i = 0; i < envelopeUpperDiff.size(); ++i) {
            envelopeDiff.push_back(abs(envelopeLowerDiff[i]+envelopeUpperDiff[i]));
        }
        auto ampMax = mathtool::max(envelopeAmp);
        auto ampMin = mathtool::min(envelopeAmp);
        auto ampMean = mathtool::mean(envelopeAmp);
        auto diffMax = mathtool::max(envelopeDiff);
        auto diffMin = mathtool::min(envelopeDiff);
        auto diffMean = mathtool::mean(envelopeDiff);
        vectord featureEnvelope = {
                ampMax, ampMin, ampMax-ampMin, ampMean,
                diffMax, diffMin, diffMax-diffMin, diffMean
        };
        envelope.first.clear();
        envelope.second.clear();
        envelopeAmp.clear();
        envelopeLowerDiff.clear();
        envelopeUpperDiff.clear();
        envelopeDiff.clear();

        //---差分计算
        vectord waveDiff10;
        for (size_t i = 0; i < noPFWave.size() - 10; i++)
        {
            auto minusValue = std::abs(noPFWave[i + 10] - noPFWave[i]);
            waveDiff10.push_back(minusValue);
        }

        vectord waveDiff25;
        for (size_t i = 0; i < noPFWave.size() - 25; i++)
        {
            auto minusValue = std::abs(noPFWave[i + 25] - noPFWave[i]);
            waveDiff25.push_back(minusValue);
        }

        vectord waveDiff50;
        for (size_t i = 0; i < noPFWave.size() - 50; i++)
        {
            auto minusValue = std::abs(noPFWave[i + 50] - noPFWave[i]);
            waveDiff50.push_back(minusValue);
        }

        vectord waveDiff125;
        for (size_t i = 0; i < noPFWave.size() - 125; i++)
        {
            auto minusValue = std::abs(noPFWave[i + 125] - noPFWave[i]);
            waveDiff125.push_back(minusValue);
        }

        vectord featureWaveDiff = {
                mathtool::max(waveDiff10), mathtool::max(waveDiff25),
                mathtool::max(waveDiff50), mathtool::max(waveDiff125)
        };

        //# ---波形复杂度
        vectord noPfWaveAbs;
        for (auto i:noPFWave) {
            noPfWaveAbs.push_back(abs(i));
        }
        auto waveAmpStd = mathtool::stdv(noPfWaveAbs);
        noPfWaveAbs.clear();

        vectord sampleWave;
        for (int i = 0; i < noPFWave.size(); i+=5) {
            sampleWave.push_back(noPFWave[i]);
        }

        vectord sampleDiffWaveTmp;
        for (int i = 0; i < sampleWave.size()-1; ++i) {
            sampleDiffWaveTmp.push_back(sampleWave[1+i]-sampleWave[i]);
        }
        vectord sampleDiffWave;
        sampleDiffWave.push_back(sampleDiffWaveTmp[0]);
        sampleDiffWave.insert(sampleDiffWave.end(), sampleDiffWaveTmp.begin(), sampleDiffWaveTmp.end());

        int zeroCrossNum = 0.;
        int thresholdCrossNum = 0.;
        for (int i = 0; i < sampleWave.size()-1; ++i) {
            if (sampleWave[i] < 0 && 0 < sampleWave[i+1])
                zeroCrossNum += 1;
            if (sampleDiffWave[i] < 0 && 0 < sampleDiffWave[i+1] && abs(sampleWave[i]) > 3*waveAmpStd)
                thresholdCrossNum += 1;
            if (sampleDiffWave[i] > 0 && 0 > sampleDiffWave[i+1] && abs(sampleWave[i]) > 3*waveAmpStd)
                thresholdCrossNum += 1;
        }

        vectori featureComplexity = {zeroCrossNum, thresholdCrossNum};

        //小波域特征计算
        auto res = mathtool::wavedec(noPFWave, "db4", 5);
        auto absCa5 = mathtool::abs(res[0]);
        auto maxCa5 = mathtool::max(absCa5);
        auto meanCa5 = mathtool::median(absCa5);
        auto absCd5 = mathtool::abs(res[1]);
        auto maxCd5 = mathtool::max(absCd5);
        auto meanCd5 = mathtool::median(absCd5);
        auto absCd4 = mathtool::abs(res[2]);
        auto maxCd4 = mathtool::max(absCd4);
        auto meanCd4 = mathtool::median(absCd4);
        auto absCd3 = mathtool::abs(res[3]);
        auto maxCd3 = mathtool::max(absCd3);
        auto meanCd3 = mathtool::median(absCd3);
        auto absCd2 = mathtool::abs(res[4]);
        auto maxCd2 = mathtool::max(absCd2);
        auto meanCd2 = mathtool::median(absCd2);
        auto absCd1 = mathtool::abs(res[5]);
        auto maxCd1 = mathtool::max(absCd1);
        auto meanCd1 = mathtool::median(absCd1);
        vectord featureWaveLet = {
            maxCa5, meanCa5,
            maxCd5, meanCd5,
            maxCd4, meanCd4,
            maxCd3, meanCd3,
            maxCd2, meanCd2,
            maxCd1, meanCd1,

        };

        // 模式特征计算
        auto arousalWave = bandpassFilter(noPFWave, 25, 45, deviceInfo->eegFs());
        auto arousalWaveAmp = mathtool::max(mathtool::abs(arousalWave));
        vectord arousalWave90(arousalWave.begin()+90, arousalWave.end()-90);
        auto arousalWavePower = fracPowerCal(arousalWave90, 125);
        auto spindleWave = bandpassFilter(noPFWave, 11, 16, deviceInfo->eegFs());
        auto spindleWaveAmp = mathtool::max(mathtool::abs(spindleWave));
        vectord spindleWave90(spindleWave.begin()+90, spindleWave.end()-90);
        auto spindleWavePower = fracPowerCal(spindleWave90, 125);

        auto logArousalPower = 20 * log10(mathtool::max(arousalWavePower));
        auto logSpindlePower = 20 * log10(mathtool::max(spindleWavePower));
        vectord featureType = {
                arousalWaveAmp, spindleWaveAmp,
                logArousalPower, logSpindlePower
        };// 特征合并

        //特征处理
        for (auto &item:featurePowerRate) {
            item = sqrt(item);
        }
        for (auto &item:featurePowerDB) {
            item = sqrt(item);
        }
        featurePowerRate[5] = log(featurePowerRate[5]);
        featureEnvelope[0] = 1.0 / featureEnvelope[0];
        featureEnvelope[2] = 1.0 / featureEnvelope[2];
        featureEnvelope[3] = 1.0 / featureEnvelope[3];
        featureEnvelope[4] = 1.0 / featureEnvelope[4];
        featureEnvelope[6] = 1.0 / featureEnvelope[6];
        featureEnvelope[7] = 1.0 / featureEnvelope[7];
        for (auto &item:featureEnvelope) {
            item = log(item);
        }
        for (auto &item:featureWaveDiff) {
            item = 1.0/item;
        }
        for (auto &item:featureWaveLet) {
            item = log(1.0/item);
        }
        for (auto &item:featureType) {
            item = 1.0 / item;
        }

        //特征合并
        vectord eegFeature(featurePowerRate.begin(), featurePowerRate.end());
        eegFeature.insert(eegFeature.end(), featurePowerDB.begin(), featurePowerDB.end());
        eegFeature.insert(eegFeature.end(), featureEnvelope.begin(), featureEnvelope.end());
        eegFeature.insert(eegFeature.end(), featureWaveDiff.begin(), featureWaveDiff.end());
        eegFeature.insert(eegFeature.end(), featureComplexity.begin(), featureComplexity.end());
        eegFeature.insert(eegFeature.end(), featureWaveLet.begin(), featureWaveLet.end());
        eegFeature.insert(eegFeature.end(), featureType.begin(), featureType.end());

        //脑电类型特征
        auto stepNoPfWaveFrom = static_cast<size_t>(6 * deviceInfo->eegFs());
        double stepWaveAmp = 0.;
        vectord absNoPfWave;
        for (size_t i = noPFWave.size()-stepNoPfWaveFrom; i < noPFWave.size(); ++i) {
            absNoPfWave.push_back(abs(noPFWave[i]));
        }
        stepWaveAmp = mathtool::max(absNoPfWave);
        absNoPfWave.clear();

        vectord arousalWaveFrac(arousalWave.end()-long(stepNoPfWaveFrom), arousalWave.end()-90);
        auto arousalFracPowerCal = fracPowerCal(arousalWaveFrac, 125);
        auto stepArousalWavePower = 20 * log10(mathtool::max(arousalFracPowerCal));
        vectord eegTypeFeature = {
                eegSpindleRate, stepWaveAmp, stepArousalWavePower
        };

        //信号质量判断
        auto qualityWave = eegLowpassFilter(noDriftWave);
        auto eegQuality = eegQualityCal(qualityWave, deviceInfo, true);
        SleepEEGFeaturesCal cal;
        cal.quality = eegQuality;
        for (auto e:eegFeature) {
            cal.eegFeature.push_back(e);
        }
        for (auto e:eegTypeFeature) {
            cal.eegTypeFeature.push_back(e);
        }
        cal.eegPower = eegPower;
        return cal;
    }

    /// @brief 冥想脑电特征
    /// @param eeglSplitData 左通道脑电数据片段
    /// @param eegrSplitData 右通道脑电数据片段
    /// @param deviceInfo 设备信息
    /// @return 频谱幅值
    vectord meditationEEGFeatures(const vectord &eeglSplitData, const vectord &eegrSplitData, DeviceInfo *deviceInfo)
    {
        // 信号处理
        auto eeglSplitDataOutlier = doubleOutlierRemove(eeglSplitData); // 跳点剔除
        auto eegrSplitDataOutlier = doubleOutlierRemove(eegrSplitData); // 跳点剔除

        auto eeglVoltData = voltageCal(eeglSplitDataOutlier, deviceInfo->eegMaxUv(), deviceInfo->eegMinUv(), deviceInfo->eegMaxVal(), deviceInfo->eegMinVal()); // 电压计算
        auto eegrVoltData = voltageCal(eegrSplitDataOutlier, deviceInfo->eegMaxUv(), deviceInfo->eegMinUv(), deviceInfo->eegMaxVal(), deviceInfo->eegMinVal()); // 电压计算
        eeglSplitDataOutlier.clear();
        eegrSplitDataOutlier.clear();
        auto noDriftWavel = eegDriftFilter(eeglVoltData); // 漂移去除
        auto noDriftWaver = eegDriftFilter(eegrVoltData); // 漂移去除
        eeglVoltData.clear();
        eegrVoltData.clear();

        auto noPFWavel = eegPfNotch(noDriftWavel); // 工频陷波
        auto noPFWaver = eegPfNotch(noDriftWaver); // 工频陷波
        noDriftWavel.clear();
        noDriftWaver.clear();

        auto lowFilterWavel = eegLowpassFilter(noPFWavel); // 低通滤波
        auto lowFilterWaver = eegLowpassFilter(noPFWaver); // 低通滤波

        auto filterWavel = eegHighpassFilter(lowFilterWavel); // 高通滤波
        auto filterWaver = eegHighpassFilter(lowFilterWaver); // 高通滤波
        lowFilterWavel.clear();
        lowFilterWaver.clear();

        auto noArtifactWavel = eegArtifactRemove(noPFWavel); // 伪迹去除
        auto noArtifactWaver = eegArtifactRemove(noPFWaver); // 伪迹去除

        auto denoiseWavel = eegWaveletDenoise(noArtifactWavel.first, "db4", "hard"); // 硬阈值去噪
        auto denoiseWaver = eegWaveletDenoise(noArtifactWaver.first, "db4", "hard"); // 硬阈值去噪
        noArtifactWavel.first.clear();
        noArtifactWavel.second.clear();
        noArtifactWaver.first.clear();
        noArtifactWaver.second.clear();

        vectord filterCutWavel(filterWavel.begin() + 90, filterWavel.end() - 90);
        vectord filterCutWaver(filterWaver.begin() + 90, filterWaver.end() - 90);

        // 频域特征计算
        auto eegPowerl = eegPowerCal(denoiseWavel, deviceInfo);
        auto eegPowerr = eegPowerCal(denoiseWaver, deviceInfo);
        denoiseWavel.clear();
        denoiseWaver.clear();
        auto alphaNorml = eegPowerl.alphaNorm();
        auto alphaNormr = eegPowerr.alphaNorm();
        auto betaNorml = eegPowerl.betaNorm();
        auto betaNormr = eegPowerr.betaNorm();
        auto thetaNorml = eegPowerl.thetaNorm();
        auto thetaNormr = eegPowerr.thetaNorm();
        auto gammaNorml = eegPowerl.gammaNorm();
        auto gammaNormr = eegPowerr.gammaNorm();
        auto deltaNorml = eegPowerl.deltaNorm();
        auto deltaNormr = eegPowerr.deltaNorm();
        auto alphaDBl = eegPowerl.alphaDB();
        auto alphaDBr = eegPowerr.alphaDB();
        auto betaDBl = eegPowerl.betaDB();
        auto betaDBr = eegPowerr.betaDB();
        auto thetaDBl = eegPowerl.thetaDB();
        auto thetaDBr = eegPowerr.thetaDB();
        auto gammaDBl = eegPowerl.gammaDB();
        auto gammaDBr = eegPowerr.gammaDB();
        auto deltaDBl = eegPowerl.deltaDB();
        auto deltaDBr = eegPowerr.deltaDB();
        auto highDBl = eegPowerl.highBetaDB();
        auto lowDBl = eegPowerl.lowBetaDB();
        auto highDBr = eegPowerr.highBetaDB();
        auto lowDBr = eegPowerr.lowBetaDB();
        auto powerDBl = eegPowerl.powerDB();
        auto powerDBr = eegPowerr.powerDB();
        vectord featurePowerRate = {
                alphaNorml, alphaNormr,
                betaNorml, betaNormr,
                thetaNorml, thetaNormr,
                deltaNorml, deltaNormr,
                gammaNorml, gammaNormr,
                eegPowerl.highBeta / eegPowerl.power, eegPowerr.highBeta / eegPowerr.power,
                eegPowerl.lowBeta / eegPowerl.power, eegPowerr.lowBeta / eegPowerr.power

        };

        auto alphaPowerAsy = std::log(alphaNormr) - std::log(alphaNorml);
        auto betaPowerAsy = std::log(betaNormr) - std::log(betaNorml);
        auto thetaPowerAsy = std::log(thetaNormr) - std::log(thetaNorml);
        auto gammaPowerAsy = std::log(gammaNormr) - std::log(gammaNorml);
        vectord featurePowerAsy = {
                alphaPowerAsy, betaPowerAsy, thetaPowerAsy, gammaPowerAsy};

        vectord featurePowerDB = {
                alphaDBl, alphaDBr,
                betaDBl, betaDBr,
                thetaDBl, thetaDBr,
                deltaDBl, deltaDBr,
                gammaDBl, gammaDBr,
                powerDBl, powerDBr,
                highDBl, highDBr,
                lowDBl, lowDBr};

        // 时域特征计算
        // ---包络特征
        auto envelopel = fracEnvelopeCal(filterCutWavel, 20);
        vectord envelopeAmpl;
        for (auto e : envelopel.first)
        {
            envelopeAmpl.push_back(std::abs(e));
        }

        for (auto e : envelopel.second)
        {
            envelopeAmpl.push_back(std::abs(e));
        }
        auto enveloper = fracEnvelopeCal(filterCutWaver, 20);
        vectord envelopeAmpr;
        for (auto e : enveloper.first)
        {
            envelopeAmpr.push_back(std::abs(e));
        }

        for (auto e : enveloper.second)
        {
            envelopeAmpr.push_back(std::abs(e));
        }
        vectord featureEnvelope = {
                mathtool::max(envelopeAmpl), mathtool::max(envelopeAmpr),
                mathtool::min(envelopeAmpl), mathtool::min(envelopeAmpr),
                mathtool::mean(envelopeAmpl), mathtool::mean(envelopeAmpr)};
        envelopel.first.clear();
        envelopel.second.clear();
        enveloper.first.clear();
        enveloper.second.clear();
        envelopeAmpl.clear();
        envelopeAmpr.clear();

        // ---差分计算
        vectord waveDiff10l;
        for (size_t i = 0; i < noPFWavel.size() - 10; i++)
        {
            auto minusValue = std::abs(noPFWavel[i + 10] - noPFWavel[i]);
            waveDiff10l.push_back(minusValue);
        }
        vectord waveDiff125l;
        for (size_t i = 0; i < noPFWavel.size() - 125; i++)
        {
            auto minusValue = std::abs(noPFWavel[i + 125] - noPFWavel[i]);
            waveDiff125l.push_back(minusValue);
        }
        vectord waveDiff10r;
        for (size_t i = 0; i < noPFWaver.size() - 10; i++)
        {
            auto minusValue = std::abs(noPFWaver[i + 10] - noPFWaver[i]);
            waveDiff10r.push_back(minusValue);
        }
        vectord waveDiff125r;
        for (size_t i = 0; i < noPFWaver.size() - 125; i++)
        {
            auto minusValue = std::abs(noPFWaver[i + 125] - noPFWaver[i]);
            waveDiff125r.push_back(minusValue);
        }
        vectord filterWaveDiff10l;
        for (size_t i = 0; i < filterWavel.size() - 10; i++)
        {
            auto minusValue = std::abs(filterWavel[i + 10] - filterWavel[i]);
            filterWaveDiff10l.push_back(minusValue);
        }
        vectord filterWaveDiff125l;
        for (size_t i = 0; i < filterWavel.size() - 125; i++)
        {
            auto minusValue = std::abs(filterWavel[i + 125] - filterWavel[i]);
            filterWaveDiff125l.push_back(minusValue);
        }
        vectord filterWaveDiff10r;
        for (size_t i = 0; i < filterWaver.size() - 10; i++)
        {
            auto minusValue = std::abs(filterWaver[i + 10] - filterWaver[i]);
            filterWaveDiff10r.push_back(minusValue);
        }
        vectord filterWaveDiff125r;
        for (size_t i = 0; i < filterWaver.size() - 125; i++)
        {
            auto minusValue = std::abs(filterWaver[i + 125] - filterWaver[i]);
            filterWaveDiff125r.push_back(minusValue);
        }
        auto wd10l = mathtool::mean(waveDiff10l);
        auto wd10r = mathtool::mean(waveDiff10r);
        auto wd125l = mathtool::mean(waveDiff125l);
        auto wd125r = mathtool::mean(waveDiff125r);
        auto fwd10l = mathtool::mean(filterWaveDiff10l);
        auto fwd10r = mathtool::mean(filterWaveDiff10r);
        auto fwd125l = mathtool::mean(filterWaveDiff125l);
        auto fwd125r = mathtool::mean(filterWaveDiff125r);
        vectord featureWaveDiff = {
                wd10l, wd10r,
                wd125l,wd125r ,
                fwd10l, fwd10r,
                fwd125l, fwd125r};

        // 小波域特征计算
        auto resl = mathtool::wavedec(noPFWavel, "db4", 5);
        auto resr = mathtool::wavedec(noPFWaver, "db4", 5);
        nc::NdArray<double> ca5l(resl.at(0));
        nc::NdArray<double> cd5l(resl.at(1));
        nc::NdArray<double> cd4l(resl.at(2));
        nc::NdArray<double> cd3l(resl.at(3));
        nc::NdArray<double> cd2l(resl.at(4));
        nc::NdArray<double> cd1l(resl.at(5));
        nc::NdArray<double> ca5r(resr.at(0));
        nc::NdArray<double> cd5r(resr.at(1));
        nc::NdArray<double> cd4r(resr.at(2));
        nc::NdArray<double> cd3r(resr.at(3));
        nc::NdArray<double> cd2r(resr.at(4));
        nc::NdArray<double> cd1r(resr.at(5));
        vectord featureWavelet = {
                nc::median(nc::abs(ca5l)).item(),
                nc::median(nc::abs(ca5r)).item(),
                nc::median(nc::abs(cd5l)).item(),
                nc::median(nc::abs(cd5r)).item(),
                nc::median(nc::abs(cd4l)).item(),
                nc::median(nc::abs(cd4r)).item(),
                nc::median(nc::abs(cd3l)).item(),
                nc::median(nc::abs(cd3r)).item(),
                nc::median(nc::abs(cd2l)).item(),
                nc::median(nc::abs(cd2r)).item(),
                nc::median(nc::abs(cd1l)).item(),
                nc::median(nc::abs(cd1r)).item(),
        };

        // 特征处理
        // 使数据高斯分布
        for (double & i : featurePowerRate)
        {
            i = std::sqrt(i);
        }

        for (double & i : featurePowerDB)
        {
            i = std::sqrt(i);
        }

        featureEnvelope[0] = 1.0 / featureEnvelope[0];
        featureEnvelope[1] = 1.0 / featureEnvelope[1];
        featureEnvelope[4] = 1.0 / featureEnvelope[4];
        featureEnvelope[5] = 1.0 / featureEnvelope[5];
        for (double & i : featureEnvelope)
        {
            i = std::log(i);
        }

        for (double & i : featureWaveDiff)
        {
            i = 1.0 / i;
        }

        for (double & i : featureWavelet)
        {
            i = std::log(1.0 / i);
        }

        // 特征合并
        vectord eegFeature(featurePowerRate); //28 29 diff
        eegFeature.insert(eegFeature.end(), featurePowerAsy.begin(), featurePowerAsy.end());
        eegFeature.insert(eegFeature.end(), featurePowerDB.begin(), featurePowerDB.end());
        eegFeature.insert(eegFeature.end(), featureEnvelope.begin(), featureEnvelope.end());
        eegFeature.insert(eegFeature.end(), featureWaveDiff.begin(), featureWaveDiff.end());
        eegFeature.insert(eegFeature.end(), featureWavelet.begin(), featureWavelet.end());

        return eegFeature;
    }

}