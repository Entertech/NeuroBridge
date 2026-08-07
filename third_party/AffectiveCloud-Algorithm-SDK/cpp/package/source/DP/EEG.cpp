#include "EEG.h"
#include "Device.h"
#include "NumCpp.hpp"
#include "MathTool.h"
#include <vector>
#include <algorithm>
using namespace basic;
using namespace dsp;
namespace dp
{

    EEGProgress::EEGProgress()
    {
        temp.index = 0;
    }

    EEGProgress::~EEGProgress()
    {
    }

    EEGTriggerRes EEGProgress::trigger(SessionCache &cache, vectord &eeglData, vectord &eegrData, bool isEar)
    {
        DeviceInfoFtV1 *deviceInfo = new DeviceInfoFtV1();

        //算法执行
        //# ---左通道脑电
        auto eeglRes = eeghandler::handler(
            eeglData,
            4.0,
            1.0,
            deviceInfo,
            temp.eeglHandlerTmp
        );
        
        auto eeglWave = eeglRes.eegWave;

        for (auto &e : eeglWave)
        {
            if (e > 500) 
                e = 500;
            else if (e < -500)
                e = -500;
        }

        auto eeglPower = mathtool::eegPowerSmoothAvg(eeglRes.power, temp.eeglPowerTmp, 0.7);
        if (isEar)
        {
            eeglPower = mathtool::eegPowerAdjust(eeglPower);
        }
        auto eeglFeaturePower = mathtool::eegPowerSmoothAvg(eeglRes.featurePower, temp.eeglFeaturePowerTmp, 0.7);
        if (isEar)
        {
            eeglFeaturePower = mathtool::eegPowerAdjust(eeglFeaturePower);
        }

        //# ---右通道脑电
        auto eegrRes = eeghandler::handler(
            eegrData,
            4.0,
            1.0,
            deviceInfo,
            temp.eegrHandlerTmp
        );
        auto eegrWave = eegrRes.eegWave;
        for (auto &e : eegrWave)
        {
            if (e > 500) 
                e = 500;
            else if (e < -500)
                e = -500;
        }
        auto eegrPower = mathtool::eegPowerSmoothAvg(eegrRes.power, temp.eegrPowerTmp, 0.7);
        if (isEar)
        {
            eegrPower = mathtool::eegPowerAdjust(eegrPower);
        }
        auto eegrFeaturePower = mathtool::eegPowerSmoothAvg(eegrRes.featurePower, temp.eegrFeaturePowerTmp, 0.7);
        if (isEar)
        {
            eegrFeaturePower = mathtool::eegPowerAdjust(eegrFeaturePower);
        }

        //---双通道脑电综合
        auto eegQuality = static_cast<int>(std::min(eeglRes.quality, eegrRes.quality));
        std::vector<EEGPower> eegPowerTmp = {eeglPower, eegrPower};
        auto eegPower = mathtool::eegMeanPowerCal(eegPowerTmp);

        std::vector<EEGPower> eegFeaturePowerTmp = {eeglFeaturePower, eegrFeaturePower};
        auto eegFeaturePower = mathtool::eegMeanPowerCal(eegFeaturePowerTmp);

        //更新内部缓存
        temp.index += 1;
        temp.eeglPowerTmp = eeglPower;
        temp.eegrPowerTmp = eegrPower;
        temp.eeglFeaturePowerTmp = eeglFeaturePower;
        temp.eegrFeaturePowerTmp = eegrFeaturePower;
        
        auto alphaDB = eegPower.alphaDB();
        auto betaDB = eegPower.betaDB();
        auto deltaDB = eegPower.deltaDB();
        auto gammaDB = eegPower.gammaDB();
        auto thetaDB = eegPower.thetaDB();
        auto lowBetaDB = eegPower.lowBetaDB();
        auto highBetaDB = eegPower.highBetaDB();
        temp.eegAlphaRec.push_back(alphaDB);
        temp.eegBetaRec.push_back(betaDB);
        temp.eegThetaRec.push_back(thetaDB);
        temp.eegDeltaRec.push_back(deltaDB);
        temp.eegGammaRec.push_back(gammaDB);
        temp.eegLowBetaRec.push_back(lowBetaDB);
        temp.eegHighBetaRec.push_back(highBetaDB);
        temp.eegQualityRec.push_back(eegQuality);
        //更新外部缓存
        cache.eegQuality = (EEGQuality)eegQuality;
        cache.eeglPower = eeglFeaturePower;
        cache.eegrPower = eegrFeaturePower;
        cache.eegPower = eegFeaturePower;

        EEGTriggerRes res;
        auto eeglRoundNdArray = nc::round(eeglWave, 2).toStlVector();
        auto eegrRoundNdArray = nc::round(eegrWave, 2).toStlVector();
        res.eeglWave.assign(eeglRoundNdArray.cbegin(), eeglRoundNdArray.cend());
        res.eegrWave.assign(eegrRoundNdArray.cbegin(), eegrRoundNdArray.cend());

        res.eegAlphaPower = nc::round(alphaDB, 4);
        res.eegBetaPower = nc::round(betaDB, 4);
        res.eegDeltaPower = nc::round(deltaDB, 4);
        res.eegGammaPower = nc::round(gammaDB, 4);
        res.eegThetaPower = nc::round(thetaDB, 4);
        res.eegLowBetaPower = nc::round(lowBetaDB, 4);
        res.eegHighBetaPower = nc::round(highBetaDB, 4);
        res.eegQuality = static_cast<int>(eegQuality);
        delete deviceInfo;
        return res;
    }

    EEGReprotRes EEGProgress::report()
    {
        EEGReprotRes res;

        if (temp.eegAlphaRec.size() < 5)
            return res;
        auto maxSmoothLen = std::max(static_cast<size_t>(temp.eegAlphaRec.size()*0.01), static_cast<size_t>(16));
        auto half_smooth_len = std::min(maxSmoothLen, temp.eegAlphaRec.size());

        
        auto eegAlphaRec = mathtool::smoothCurveCal(temp.eegAlphaRec, int(half_smooth_len));
        auto tempAlphaRec = nc::NdArray<double>(eegAlphaRec);
        auto vTempAlpha = nc::round(tempAlphaRec, 4).toStlVector();
        res.eegAlphaRec.assign(vTempAlpha.cbegin(), vTempAlpha.cend());

        auto eegBetaRec = mathtool::smoothCurveCal(temp.eegBetaRec, int(half_smooth_len));
        auto tempBetaRec = nc::NdArray<double>(eegBetaRec);
        auto vTempBeta = nc::round(tempBetaRec, 4).toStlVector();
        res.eegBetaRec.assign(vTempBeta.cbegin(), vTempBeta.cend());

        auto eegThetaRec = mathtool::smoothCurveCal(temp.eegThetaRec, int(half_smooth_len));
        auto tempThetaRec = nc::NdArray<double>(eegThetaRec);
        auto vTempTheta = nc::round(tempThetaRec, 4).toStlVector();
        res.eegThetaRec.assign(vTempTheta.cbegin(), vTempTheta.cend());

        auto eegDeltaRec = mathtool::smoothCurveCal(temp.eegDeltaRec, int(half_smooth_len));
        auto tempDeltaRec = nc::NdArray<double>(eegDeltaRec);
        auto vTempDelta = nc::round(tempDeltaRec, 4).toStlVector();
        res.eegDeltaRec.assign(vTempDelta.cbegin(), vTempDelta.cend());

        auto eegGammaRec = mathtool::smoothCurveCal(temp.eegGammaRec, int(half_smooth_len));
        auto tempGammaRec = nc::NdArray<double>(eegGammaRec);
        auto vTempGamma = nc::round(tempGammaRec, 4).toStlVector();
        res.eegGammaRec.assign(vTempGamma.cbegin(), vTempGamma.cend());

        auto eegLowBetaRec = mathtool::smoothCurveCal(temp.eegLowBetaRec, int(half_smooth_len));
        auto tempLowBetaRec = nc::NdArray<double>(eegLowBetaRec);
        auto vTempLowBeta = nc::round(tempLowBetaRec, 4).toStlVector();
        res.eegLowBetaRec.assign(vTempLowBeta.cbegin(), vTempLowBeta.cend());

        auto eegHighBetaRec = mathtool::smoothCurveCal(temp.eegHighBetaRec, int(half_smooth_len));
        auto tempHighBetaRec = nc::NdArray<double>(eegHighBetaRec);
        auto vTempHighBeta = nc::round(tempHighBetaRec, 4).toStlVector();
        res.eegHighBetaRec.assign(vTempHighBeta.cbegin(), vTempHighBeta.cend());

        res.eegQualityRec.assign(temp.eegQualityRec.cbegin(), temp.eegQualityRec.cend());
        return res;
    }

}