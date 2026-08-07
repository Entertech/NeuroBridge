//
// Created by Enter M1 on 2023/7/11.
//
#include "SCEEG.h"
#include "Device.h"
#include "NumCpp.hpp"
#include "MathTool.h"
#include <algorithm>
using namespace basic;
using namespace dsp;
namespace dp
{

    SCEEGProcess::SCEEGProcess()
    {
        temp.index = 0;
    }

    SCEEGProcess::~SCEEGProcess()
    {
    }


    SCEEGTriggerRes SCEEGProcess::trigger(SessionCache &cache, vectord &eegData)
    {
        DeviceInfoEyeShade *deviceInfo = new DeviceInfoEyeShade();

        //算法执行
        //# ---左通道脑电
        auto eegRes = eeghandler::handler(
                eegData,
                4.0,
                1.0,
                deviceInfo,
                temp.eegHandlerTmp
        );

        auto eegWave = eegRes.eegWave;

        for (auto &e : eegWave)
        {
            if (e > 500)
                e = 500;
            else if (e < -500)
                e = -500;
        }
        auto eegPowerPre = mathtool::eegPowerSmoothAvg(eegRes.power, temp.eegPowerTmp, 0.7);
        auto eegPower = mathtool::eegPowerAdjust(eegPowerPre);
        auto eegFeaturePowerPre = mathtool::eegPowerSmoothAvg(eegRes.featurePower, temp.eegFeaturePowerTmp, 0.7);
        auto eegFeaturePower = mathtool::eegPowerAdjust(eegFeaturePowerPre);
        auto eegQuality = static_cast<int>(eegRes.quality);

        //更新内部缓存
        temp.index += 1;
        temp.eegPowerTmp = eegPower;
        temp.eegFeaturePowerTmp = eegFeaturePower;

        temp.eegAlphaRec.push_back(eegPower.alphaDB());
        temp.eegBetaRec.push_back(eegPower.betaDB());
        temp.eegThetaRec.push_back(eegPower.thetaDB());
        temp.eegDeltaRec.push_back(eegPower.deltaDB());
        temp.eegGammaRec.push_back(eegPower.gammaDB());
        temp.eegQualityRec.push_back(eegQuality);
        //更新外部缓存
        cache.eegQuality = (EEGQuality)eegQuality;
        cache.eegPower = eegFeaturePower;

        // 输出结果
        SCEEGTriggerRes res;
        res.eegWave.assign(eegWave.begin(), eegWave.end());

        auto alphaDB = eegPower.alphaDB();
        auto betaDB = eegPower.betaDB();
        auto deltaDB = eegPower.deltaDB();
        auto gammaDB = eegPower.gammaDB();
        auto thetaDB = eegPower.thetaDB();


        res.eegAlphaPower = nc::round(alphaDB, 4);
        res.eegBetaPower = nc::round(betaDB, 4);
        res.eegDeltaPower = nc::round(deltaDB, 4);
        res.eegGammaPower = nc::round(gammaDB, 4);
        res.eegThetaPower = nc::round(thetaDB, 4);
        res.eegQuality = static_cast<int>(eegQuality);
        delete deviceInfo;
        return res;

    }

    SCEEGReportRes SCEEGProcess::report()
    {
        SCEEGReportRes res;
        //# 长度检验
        if (temp.eegAlphaRec.size() < 5)
            return res;

        //        # 算法执行
        //        # ---曲线平滑
        auto maxSmoothLen = std::max(static_cast<size_t>(temp.eegAlphaRec.size()*0.01), static_cast<size_t>(16));
        auto half_smooth_len = std::min(maxSmoothLen, temp.eegAlphaRec.size());

        //         # 输出结果
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
        res.eegQualityRec.assign(temp.eegQualityRec.cbegin(), temp.eegQualityRec.cend());
        return res;
    }


}