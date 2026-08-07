//
// Created by Enter M1 on 2023/7/11.
//

#ifndef AFFECTIVECPP_SCEEG_H
#define AFFECTIVECPP_SCEEG_H
#include "Data.h"
#include "EEGHandler.h"
#include "SessionCache.h"
#include "TypeDefine.h"
#include "MathTool.h"
namespace dp
{
    /*
     * 单通道脑电实时触发输出
     */
    struct SCEEGTriggerRes
    {
        vectord eegWave; //脑电波形

        double eegAlphaPower; //脑电α频段能量
        double eegBetaPower; //脑电β频段能量
        double eegThetaPower; //脑电θ频段能量
        double eegDeltaPower; //脑电δ频段能量
        double eegGammaPower; //脑电γ频段能量
        int eegQuality; //脑电质量等级
    };

    /*
     * 单通道脑电报表计算输出
     */
    struct SCEEGReportRes
    {
        vectord eegAlphaRec; //脑电α波能量全程变化曲线
        vectord eegBetaRec;  //脑电β波能量全程变化曲线
        vectord eegThetaRec; //脑电θ波能量全程变化曲线
        vectord eegDeltaRec; //脑电δ波能量全程变化曲线
        vectord eegGammaRec; //脑电γ波能量全程变化曲线
        vectori eegQualityRec; //脑电质量等级全程变化曲线
    };

    /*
     * 单通道脑电数据处理缓存
     */
    struct SCEEGTmp
    {
        int index; //计数
        basic::dsp::eeghandler::EEGHandlerTemp eegHandlerTmp; //单通道脑电处理器缓存
        basic::dsp::EEGPower eegPowerTmp; //单通道脑电能量缓存，用于平滑
        basic::dsp::EEGPower eegFeaturePowerTmp; //单通道脑电特征能量缓存，用于平滑
        vectord eegAlphaRec;
        vectord eegBetaRec;
        vectord eegThetaRec;
        vectord eegDeltaRec;
        vectord eegGammaRec;
        vectori eegQualityRec;
    };

    class SCEEGProcess
    {
    public:
        /**
         * 单通道脑电数据处理
         * @param cache
         * @param eegData
         * @return
         */
        SCEEGTriggerRes trigger(basic::SessionCache &cache, vectord &eegData);

        SCEEGReportRes report();
        SCEEGProcess();
        ~SCEEGProcess();

    private:
        SCEEGTmp temp;
    };

} // namespace dp
#endif //AFFECTIVECPP_SCEEG_H
