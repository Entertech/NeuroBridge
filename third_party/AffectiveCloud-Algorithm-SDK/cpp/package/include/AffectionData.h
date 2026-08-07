#ifndef C392024C_B929_4232_89F2_323EBDCD17F4
#define C392024C_B929_4232_89F2_323EBDCD17F4

namespace basic
{
    namespace affection
    {
        namespace define
        {
            //睡眠实时相位
            enum SleepPhaseEnum
            {
                AWAKE = 0,
                UNKNOWN = 1,
                ASLEEP = 2
            };

            //睡眠状态
            enum SleepStateEnum
            {
                AWAKES = 0,
                ASLEEPS = 1
            };

            //睡眠分期
            enum SleepStage
            {
                WAKE = 0,  // 清醒
                NREM1 = 1,  // 非快速眼动期1（思睡期）
                NREM2 = 2,  // 非快速眼动期2（浅睡期）
                NREM3 = 3,  // 非快速眼动期3（深睡期）
                REM = 4,  // 快速眼动期
            };

            enum RelaxationStateEnum
            {
                NERVOUS = -1,
                UNKNOWNS = 0,
                RELAXED = 1
            };

            enum AttentionState
            {
                DISTRACTED = -1, //分心 
                UNKNOWNA = 0, //未知 
                ATTENTIVE = 1 //专注
            };

            enum MeditationState
            {
                ACTIVE = 0,  // 活跃
                FLOW = 1     // 心流
            };
        } // namespace define

    } // namespace affection

} // namespace basic

#endif /* C392024C_B929_4232_89F2_323EBDCD17F4 */
